> **Historical (as of 2026-09-25)** — superseded by the elysia-rbac repo (Plane A RBAC, Drizzle schema in `src/db/schema/`). Kept for context; do not treat as current.

# RBAC System Design

## 1. Overview

This document describes a flexible Role-Based Access Control system that supports:

- Custom users
- Custom groups
- Custom roles
- Custom permissions
- Permission scopes
- Parent-child group hierarchy
- Role inheritance through groups
- Direct user-role assignment
- Explicit allow and deny rules
- Effective permission calculation
- Auditable authorization decisions

The goal is to build an authorization system where administrators can define who can perform which actions, on which resources, and within which scope.

---

## 2. Core Idea

The system should be built around this relationship:

```txt
User -> Groups -> Roles -> Permissions -> Scopes
```

A user belongs to one or more groups. Groups can have parent groups. Roles can be assigned to users or groups. Roles contain permissions. Permissions are evaluated against a scope.

Example:

```txt
Company
└── Engineering
    └── Backend Team
        └── Payments Team
```

If a user belongs to `Payments Team`, they can inherit roles from:

```txt
Payments Team
Backend Team
Engineering
Company
```

This makes it possible to define permissions at a broad level and override or extend them at a more specific level.

---

## 3. Main Entities

## 3.1 User

A user represents a person or service account in the system.

Example fields:

```txt
users
- id
- name
- email
- status
- created_at
- updated_at
```

Recommended statuses:

```txt
active
inactive
suspended
pending
```

Users should usually receive permissions through groups instead of direct role assignments. Direct user-role assignments should be used for exceptions.

---

## 3.2 Group

A group represents a collection of users. Groups may also have a parent group.

Examples:

```txt
Company
Engineering
Backend Team
Payments Team
Finance
Support
Project Alpha Team
```

Example fields:

```txt
groups
- id
- name
- description
- parent_group_id nullable
- created_at
- updated_at
```

Example hierarchy:

```txt
Company
├── Engineering
│   ├── Backend Team
│   │   └── Payments Team
│   └── Frontend Team
└── Finance
```

A child group can inherit roles from its parent groups.

---

## 3.3 User Group Membership

A user can belong to multiple groups.

Example table:

```txt
user_groups
- id
- user_id
- group_id
- created_at
```

Example:

```txt
Ayush -> Payments Team
Priya -> Finance
John -> Support
```

If Ayush belongs to `Payments Team`, he may inherit roles from `Payments Team`, `Backend Team`, `Engineering`, and `Company`.

---

## 3.4 Role

A role is a named collection of permissions.

Examples:

```txt
Admin
Manager
Developer
Viewer
Editor
Billing Manager
Project Owner
Support Agent
```

Example fields:

```txt
roles
- id
- name
- description
- is_system_role
- created_at
- updated_at
```

`is_system_role` can be used to protect default roles from being deleted or modified accidentally.

Example roles:

```txt
Role: Viewer
- project.read
- task.read
- comment.read

Role: Project Manager
- project.read
- project.update
- task.create
- task.assign
- user.invite

Role: Billing Manager
- invoice.read
- invoice.approve
- payment.refund
```

---

## 3.5 Permission

A permission defines an action that can be performed on a resource.

A clean permission format is:

```txt
resource.action
```

Examples:

```txt
project.read
project.create
project.update
project.delete
invoice.read
invoice.approve
user.invite
role.assign
group.create
```

Example fields:

```txt
permissions
- id
- resource
- action
- description
- created_at
- updated_at
```

Recommended resources:

```txt
user
group
role
permission
project
task
invoice
payment
report
setting
```

Recommended actions:

```txt
create
read
update
delete
approve
reject
assign
invite
export
import
manage
```

---

## 3.6 Role Permission Mapping

Roles contain many permissions.

Example table:

```txt
role_permissions
- id
- role_id
- permission_id
- effect
- created_at
```

The `effect` field should support:

```txt
allow
deny
```

Example:

```txt
Project Manager -> project.read -> allow
Project Manager -> project.update -> allow
Project Manager -> project.delete -> deny
```

Explicit deny is useful when a broad role grants access but a specific role or group should block it.

---

## 4. Scopes

A scope defines where a permission applies.

Examples:

```txt
global
organization
department
team
project
own
```

Example:

```txt
invoice.read at organization scope
project.update at project scope
user.update at own scope
```

This allows the system to distinguish between:

```txt
Can read all invoices globally
Can read invoices only in one organization
Can update only one project
Can update only own profile
```

---

## 5. Scope Model

A role assignment should include a scope.

Example table for group roles:

```txt
group_roles
- id
- group_id
- role_id
- scope_type
- scope_id nullable
- created_at
- expires_at nullable
```

Example table for direct user roles:

```txt
user_roles
- id
- user_id
- role_id
- scope_type
- scope_id nullable
- created_at
- expires_at nullable
```

Examples:

```txt
Engineering -> Developer -> organization:acme
Payments Team -> Payments Manager -> project:payments
Ayush -> Emergency Admin -> project:alpha
```

For global scope, `scope_id` can be null:

```txt
Super Admin -> global:null
```

---

## 6. Scope Hierarchy

A simple scope hierarchy can be:

```txt
global
└── organization
    └── department
        └── team
            └── project
                └── own
```

A broader scope can include narrower scopes.

Example:

```txt
organization:acme
```

can include:

```txt
department:engineering
team:payments
project:alpha
```

But:

```txt
project:alpha
```

should not include:

```txt
project:beta
organization:acme
```

---

## 7. Group Inheritance

Groups can inherit roles from parent groups.

Example group table:

```txt
id | name          | parent_group_id
---|---------------|----------------
1  | Company       | null
2  | Engineering   | 1
3  | Backend Team  | 2
4  | Payments Team | 3
```

If a user belongs to `Payments Team`, the effective group chain is:

```txt
Payments Team -> Backend Team -> Engineering -> Company
```

The user receives roles assigned to all groups in this chain.

---

## 8. Recursive Group Query

In PostgreSQL, parent groups can be resolved with a recursive query.

```sql
WITH RECURSIVE group_tree AS (
  SELECT id, name, parent_group_id
  FROM groups
  WHERE id = :group_id

  UNION ALL

  SELECT g.id, g.name, g.parent_group_id
  FROM groups g
  JOIN group_tree gt ON g.id = gt.parent_group_id
)
SELECT * FROM group_tree;
```

For a user with multiple groups:

```sql
WITH RECURSIVE user_group_tree AS (
  SELECT g.id, g.name, g.parent_group_id
  FROM groups g
  JOIN user_groups ug ON ug.group_id = g.id
  WHERE ug.user_id = :user_id

  UNION ALL

  SELECT parent.id, parent.name, parent.parent_group_id
  FROM groups parent
  JOIN user_group_tree child ON parent.id = child.parent_group_id
)
SELECT DISTINCT * FROM user_group_tree;
```

---

## 9. Access Decision Priority

Recommended authorization priority:

```txt
1. Explicit deny
2. Direct user allow
3. Group allow
4. Parent group allow
5. Default deny
```

Default should always be deny.

If nothing explicitly allows an action, the request should be blocked.

---

## 10. Authorization Check Flow

When a user tries to perform an action:

```txt
Can Ayush approve payment in project:payments?
```

The system should:

```txt
1. Get direct roles assigned to the user.
2. Get groups the user belongs to.
3. Get parent groups recursively.
4. Get roles assigned to all effective groups.
5. Get permissions attached to all roles.
6. Filter permissions by resource and action.
7. Check whether the assignment scope matches the target scope.
8. Apply explicit deny rules.
9. Return allow or deny.
```

Pseudo-code:

```ts
async function can(userId, resource, action, targetScope) {
  const directRoles = await getDirectUserRoles(userId)
  const groups = await getUserGroups(userId)
  const parentGroups = await getParentGroupsRecursive(groups)
  const groupRoles = await getGroupRoles([...groups, ...parentGroups])

  const assignments = [...directRoles, ...groupRoles]
  const permissions = await getPermissionsForAssignments(assignments)

  const matchingPermissions = permissions.filter(permission => {
    return permission.resource === resource &&
           permission.action === action &&
           scopeMatches(permission.scope, targetScope)
  })

  const hasDeny = matchingPermissions.some(permission => permission.effect === 'deny')
  if (hasDeny) {
    return {
      allowed: false,
      reason: 'Explicit deny matched'
    }
  }

  const hasAllow = matchingPermissions.some(permission => permission.effect === 'allow')
  if (hasAllow) {
    return {
      allowed: true,
      reason: 'Allow permission matched'
    }
  }

  return {
    allowed: false,
    reason: 'Default deny'
  }
}
```

---

## 11. Scope Matching Logic

The scope matching function decides whether a permission assigned at one scope applies to a target resource.

Example:

```txt
Assigned role: Project Manager
Assigned scope: project:alpha
Requested action: project.update
Target scope: project:alpha
Result: allowed
```

But:

```txt
Assigned role: Project Manager
Assigned scope: project:alpha
Requested action: project.update
Target scope: project:beta
Result: denied
```

Pseudo-code:

```ts
function scopeMatches(assignedScope, targetScope) {
  if (assignedScope.type === 'global') {
    return true
  }

  if (assignedScope.type === targetScope.type && assignedScope.id === targetScope.id) {
    return true
  }

  return isParentScope(assignedScope, targetScope)
}
```

Example parent scope logic:

```ts
function isParentScope(parentScope, childScope) {
  // Example:
  // organization:acme includes project:payments
  // department:engineering includes team:backend
  // team:backend includes project:payments

  const ancestors = getScopeAncestors(childScope)

  return ancestors.some(scope => {
    return scope.type === parentScope.type && scope.id === parentScope.id
  })
}
```

---

## 12. Recommended Database Schema

## 12.1 Users

```sql
CREATE TABLE users (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

## 12.2 Groups

```sql
CREATE TABLE groups (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  parent_group_id UUID REFERENCES groups(id),
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

## 12.3 User Groups

```sql
CREATE TABLE user_groups (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id),
  group_id UUID NOT NULL REFERENCES groups(id),
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  UNIQUE(user_id, group_id)
);
```

## 12.4 Roles

```sql
CREATE TABLE roles (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  is_system_role BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

## 12.5 Permissions

```sql
CREATE TABLE permissions (
  id UUID PRIMARY KEY,
  resource TEXT NOT NULL,
  action TEXT NOT NULL,
  description TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
  UNIQUE(resource, action)
);
```

## 12.6 Role Permissions

```sql
CREATE TABLE role_permissions (
  id UUID PRIMARY KEY,
  role_id UUID NOT NULL REFERENCES roles(id),
  permission_id UUID NOT NULL REFERENCES permissions(id),
  effect TEXT NOT NULL CHECK (effect IN ('allow', 'deny')),
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  UNIQUE(role_id, permission_id)
);
```

## 12.7 Group Roles

```sql
CREATE TABLE group_roles (
  id UUID PRIMARY KEY,
  group_id UUID NOT NULL REFERENCES groups(id),
  role_id UUID NOT NULL REFERENCES roles(id),
  scope_type TEXT NOT NULL,
  scope_id UUID,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMP,
  UNIQUE(group_id, role_id, scope_type, scope_id)
);
```

## 12.8 User Roles

```sql
CREATE TABLE user_roles (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id),
  role_id UUID NOT NULL REFERENCES roles(id),
  scope_type TEXT NOT NULL,
  scope_id UUID,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMP,
  UNIQUE(user_id, role_id, scope_type, scope_id)
);
```

## 12.9 Optional Audit Log

```sql
CREATE TABLE authorization_audit_logs (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id),
  resource TEXT NOT NULL,
  action TEXT NOT NULL,
  target_scope_type TEXT NOT NULL,
  target_scope_id UUID,
  allowed BOOLEAN NOT NULL,
  reason TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## 13. API Design

## 13.1 Groups

```txt
POST   /groups
GET    /groups
GET    /groups/:id
PATCH  /groups/:id
DELETE /groups/:id
POST   /groups/:id/users
DELETE /groups/:id/users/:userId
POST   /groups/:id/roles
DELETE /groups/:id/roles/:roleId
```

Create group request:

```json
{
  "name": "Payments Team",
  "description": "Handles payment-related systems",
  "parentGroupId": "backend-team-id"
}
```

Assign user to group:

```json
{
  "userId": "user_123"
}
```

Assign role to group:

```json
{
  "roleId": "role_project_manager",
  "scope": {
    "type": "project",
    "id": "project_payments"
  }
}
```

---

## 13.2 Roles

```txt
POST   /roles
GET    /roles
GET    /roles/:id
PATCH  /roles/:id
DELETE /roles/:id
POST   /roles/:id/permissions
DELETE /roles/:id/permissions/:permissionId
```

Create role request:

```json
{
  "name": "Project Manager",
  "description": "Can manage projects and assign tasks"
}
```

Attach permission to role:

```json
{
  "permissionId": "permission_project_update",
  "effect": "allow"
}
```

---

## 13.3 Permissions

```txt
POST   /permissions
GET    /permissions
GET    /permissions/:id
PATCH  /permissions/:id
DELETE /permissions/:id
```

Create permission request:

```json
{
  "resource": "project",
  "action": "update",
  "description": "Allows updating project details"
}
```

---

## 13.4 User Roles

```txt
POST   /users/:id/roles
DELETE /users/:id/roles/:roleId
GET    /users/:id/effective-roles
GET    /users/:id/effective-permissions
```

Assign direct role to user:

```json
{
  "roleId": "role_emergency_admin",
  "scope": {
    "type": "project",
    "id": "project_alpha"
  },
  "expiresAt": "2026-12-31T23:59:59Z"
}
```

---

## 13.5 Authorization Check

```txt
POST /authz/check
```

Request:

```json
{
  "userId": "user_123",
  "resource": "payment",
  "action": "approve",
  "scope": {
    "type": "project",
    "id": "project_payments"
  }
}
```

Response:

```json
{
  "allowed": true,
  "reason": "Allowed through Payments Team -> Payments Manager role",
  "matchedRole": "Payments Manager",
  "matchedGroup": "Payments Team",
  "matchedPermission": "payment.approve"
}
```

Denied response:

```json
{
  "allowed": false,
  "reason": "Default deny. No matching permission found."
}
```

Explicit deny response:

```json
{
  "allowed": false,
  "reason": "Explicit deny matched from Restricted Billing role"
}
```

---

## 14. Example Full Scenario

## 14.1 Groups

```txt
Company
└── Engineering
    └── Backend Team
        └── Payments Team
```

## 14.2 Users

```txt
Ayush -> Payments Team
```

## 14.3 Roles

```txt
Company Admin
Developer
Payments Manager
Viewer
```

## 14.4 Permissions

```txt
Company Admin:
- *.*

Developer:
- repo.read
- repo.write
- deployment.read

Payments Manager:
- payment.read
- payment.approve
- refund.create

Viewer:
- project.read
- task.read
```

## 14.5 Role Assignments

```txt
Engineering -> Developer -> organization:acme
Payments Team -> Payments Manager -> project:payments
Ayush -> Viewer -> project:alpha
```

## 14.6 Effective Access

Ayush can:

```txt
repo.write in organization:acme
payment.approve in project:payments
project.read in project:alpha
```

Ayush cannot:

```txt
payment.approve in project:beta
user.delete globally
role.assign globally
```

---

## 15. Wildcard Permissions

You may optionally support wildcard permissions.

Examples:

```txt
*.*
project.*
*.read
```

Meaning:

```txt
*.*       -> all actions on all resources
project.* -> all actions on project resource
*.read    -> read action on all resources
```

Use wildcard permissions carefully. They are powerful and should usually be limited to admin roles.

---

## 16. Deny Rules

Deny rules are useful for exceptions.

Example:

```txt
Engineering group has project.delete allow
Interns group has project.delete deny
```

If a user belongs to both groups, deny should win.

Recommended rule:

```txt
Explicit deny always overrides allow.
```

This prevents accidental privilege escalation.

---

## 17. Caching Effective Permissions

For performance, calculating effective permissions on every request may become expensive.

Possible solutions:

1. Cache effective permissions per user.
2. Cache group inheritance trees.
3. Cache role-permission mappings.
4. Invalidate cache when groups, roles, permissions, or assignments change.

Example cache key:

```txt
rbac:effective_permissions:user_123
```

Cache invalidation should happen when:

```txt
User group membership changes
Group parent changes
Role permissions change
Group role assignment changes
User role assignment changes
Permission definition changes
```

---

## 18. Audit Logs

Authorization systems should be auditable.

Log important events such as:

```txt
Role created
Role deleted
Permission added to role
Permission removed from role
User added to group
User removed from group
Role assigned to group
Role removed from group
Direct role assigned to user
Direct role removed from user
Authorization check denied
```

Audit log fields:

```txt
actor_user_id
operation
target_type
target_id
old_value
new_value
created_at
```

Example:

```json
{
  "actorUserId": "admin_1",
  "operation": "ROLE_ASSIGNED_TO_GROUP",
  "targetType": "group",
  "targetId": "payments_team",
  "newValue": {
    "role": "Payments Manager",
    "scope": "project:payments"
  },
  "createdAt": "2026-05-21T10:00:00Z"
}
```

---

## 19. Security Recommendations

Use these rules to keep the RBAC system safe:

1. Default deny all access.
2. Prefer group-based role assignments over direct user-role assignments.
3. Use direct user roles only for exceptions or temporary access.
4. Add expiration support for sensitive assignments.
5. Keep admin roles separate from normal business roles.
6. Log all role and permission changes.
7. Require stronger checks for assigning admin roles.
8. Prevent users from modifying their own high-privilege roles.
9. Use explicit deny carefully and consistently.
10. Validate group hierarchy to prevent cycles.
11. Avoid giving wildcard permissions to normal users.
12. Periodically review effective permissions.

---

## 20. Preventing Group Cycles

Since groups can have parents, you must prevent cycles.

Invalid example:

```txt
Company -> Engineering -> Backend -> Company
```

This creates an infinite inheritance loop.

Before setting a parent group, check whether the proposed parent is already a descendant of the group.

Pseudo-code:

```ts
function canSetParent(groupId, newParentGroupId) {
  const descendants = getAllDescendantGroups(groupId)
  return !descendants.includes(newParentGroupId)
}
```

---

## 21. MVP Version

For the first version, build only the core features:

```txt
Users
Groups
Parent groups
User group membership
Roles
Permissions
Role permissions
Group role assignments
User role assignments
Scoped access checks
```

Avoid adding too much at the beginning.

Do not start with:

```txt
Complex ABAC rules
Approval workflows
Temporary delegation
Custom scripting
Policy language
Dynamic conditions
```

You can add those later after the basic RBAC system is stable.

---

## 22. Future Enhancements

After the MVP, you can add:

```txt
Temporary role assignments
Approval flow for sensitive roles
Role request system
Permission review dashboard
ABAC-style conditions
Policy simulator
Access explanation UI
Access diff preview
Emergency break-glass access
Webhook events
SCIM integration
SSO group sync
```

Example condition-based permission:

```txt
Allow invoice.approve only if invoice.amount < 10000
```

This moves the system closer to ABAC, so it should be added carefully.

---

## 23. Recommended Final Rule

The core authorization rule should be:

```txt
A user is allowed to perform an action only if at least one active direct or inherited role grants the required permission for the target scope, and no matching explicit deny exists.
```

In simpler words:

```txt
No permission means no access.
Deny beats allow.
Scope must match.
Parent groups can provide inherited roles.
```

---

## 24. Summary

A strong RBAC system should be based on these principles:

```txt
Users belong to groups.
Groups can have parent groups.
Roles are assigned to users or groups.
Roles contain permissions.
Permissions are scoped.
Child groups inherit from parent groups.
Access is denied by default.
Explicit deny overrides allow.
Every sensitive change should be audited.
```

This design gives you a flexible foundation for custom groups, custom roles, scoped permissions, inheritance, and parent-child relationships.
