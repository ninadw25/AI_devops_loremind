"""Jira discovery: identity, project ACL, and issue-security crawl."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from common import scopes as scope_ids   # aliased: `scopes` is a local var name here
from common.output_paths import JiraPaths

logger = logging.getLogger(__name__)


class JiraDiscovery:
    def __init__(self, fetcher: Any, org: str, output_dir: Path = Path("output")):
        self.fetcher = fetcher
        self.org = org
        self.paths = JiraPaths(org, data_dir=output_dir)
        self.paths.makedirs()

    async def phase_1_identity_crawl(self) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, Any]]]:
        logger.info("[%s] Phase 1: Identity crawl starting", self.org)

        principals: Dict[str, Dict[str, str]] = {}
        groups: Dict[str, Dict[str, Any]] = {}

        start_at = 0
        max_results = 50
        while True:
            try:
                resp = await self.fetcher.get("/rest/api/3/users/search", params={"startAt": start_at, "maxResults": max_results})
            except Exception as exc:
                logger.warning("[%s] User crawl unavailable (%s); continuing with empty principals", self.org, exc)
                break
            if not resp:
                break
            users_page = resp if isinstance(resp, list) else resp.get("values", resp.get("users", resp))
            if not users_page:
                break
            for user in users_page:
                account_id = user.get("accountId") or user.get("account_id")
                if not account_id:
                    continue
                principals[account_id] = {
                    "principal_id": scope_ids.jira_user(account_id),
                    "email": user.get("emailAddress") or user.get("email") or "",
                    "display_name": user.get("displayName") or user.get("display_name") or "",
                    "account_type": user.get("accountType", "atlassian"),
                }
            if isinstance(users_page, list) and len(users_page) < max_results:
                break
            if isinstance(resp, dict) and "startAt" in resp and "total" in resp:
                start_at = resp.get("startAt", 0) + len(users_page)
                if start_at >= resp.get("total", 0):
                    break
            else:
                if isinstance(users_page, list):
                    start_at += len(users_page)
                    continue
                break

        logger.info("[%s] Fetched %d principals", self.org, len(principals))

        group_candidates = ["/rest/api/3/group/bulk", "/rest/api/3/groups/picker", "/rest/api/3/groups"]
        groups_list = None
        for ep in group_candidates:
            try:
                resp = await self.fetcher.get(ep, params={"maxResults": 100})
                if not resp:
                    continue
                if isinstance(resp, dict):
                    if "values" in resp:
                        groups_list = resp["values"]
                        break
                    if "groups" in resp:
                        groups_list = resp["groups"]
                        break
                elif isinstance(resp, list):
                    groups_list = resp
                    break
            except Exception:
                continue

        if not groups_list:
            logger.info("[%s] No groups list returned by Jira; continuing with empty groups.", self.org)
            groups_list = []

        for g in groups_list:
            raw_gid = g.get("groupId") or g.get("id") or g.get("name")
            if not raw_gid:
                continue
            group_id = scope_ids.jira_group(raw_gid)
            groups[group_id] = {
                "group_id": group_id,
                "name": g.get("name") or g.get("groupName") or str(raw_gid),
                "members": [],
            }

        logger.info("[%s] Discovered %d groups (ids) — fetching members", self.org, len(groups))

        for group_id in list(groups.keys()):
            # Strip jira:G prefix for Jira API calls — the API expects raw UUIDs/names
            api_gid = group_id.removeprefix(scope_ids.JIRA_GROUP_PREFIX)
            members: List[str] = []
            start_at = 0
            while True:
                try:
                    resp = await self.fetcher.get(
                        "/rest/api/3/group/member",
                        params={"groupId": api_gid, "startAt": start_at, "maxResults": 50},
                    )
                except Exception:
                    try:
                        resp = await self.fetcher.get(
                            f"/rest/api/3/groups/{api_gid}/members",
                            params={"startAt": start_at, "maxResults": 50},
                        )
                    except Exception as exc:
                        logger.warning(
                            "[%s] Group %s member fetch failed (%s); this group's "
                            "members will be incomplete — grants may be missing.",
                            self.org, group_id, exc,
                        )
                        resp = None
                if not resp:
                    break
                members_page = resp if isinstance(resp, list) else resp.get("values", resp.get("members", []))
                if not members_page:
                    break
                for m in members_page:
                    acct = m.get("accountId") or m.get("account_id")
                    if acct:
                        members.append(acct)
                if isinstance(resp, dict) and "isLast" in resp:
                    if resp.get("isLast"):
                        break
                    start_at += len(members_page)
                    continue
                if isinstance(members_page, list) and len(members_page) < 50:
                    break
                start_at += len(members_page)
            groups[group_id]["members"] = members

        logger.info("[%s] Phase 1 complete: %d principals, %d groups", self.org, len(principals), len(groups))
        return principals, groups

    async def phase_2_project_acl_crawl(self, principals: Dict[str, Dict[str, str]], groups: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        logger.info("Phase 2: Project ACL crawl starting")

        scopes: List[Dict[str, Any]] = []
        scope_principals: List[Dict[str, Any]] = []
        group_members: List[Dict[str, Any]] = []
        project_permission_rules: Dict[str, Dict[str, Any]] = {}
        acl_incomplete_projects: List[Dict[str, Any]] = []
        seen_scope_rows: set[tuple[str, str, str]] = set()
        seen_group_rows: set[tuple[str, str]] = set()

        # Name->UUID lookup: Jira Cloud permission APIs return group name in
        # "parameter" but group UUID in "value". group_members uses UUIDs (from
        # Phase 1 /group/bulk), so we must normalise grant rows to UUIDs too.
        group_name_to_id: dict[str, str] = {
            g.get("name", ""): gid
            for gid, g in groups.items()
            if g.get("name")
        }

        for group_id, group in groups.items():
            for member_id in group.get("members", []):
                key = (group_id, member_id)
                if key in seen_group_rows:
                    continue
                seen_group_rows.add(key)
                group_members.append({"group_id": group_id, "principal_id": scope_ids.jira_user(member_id)})

        projects_resp = await self.fetcher.list_projects()
        projects = []
        if isinstance(projects_resp, dict):
            projects = projects_resp.get("values") or projects_resp.get("projects") or projects_resp.get("project") or []
        elif isinstance(projects_resp, list):
            projects = projects_resp

        for project in projects:
            project_key = project.get("key") or project.get("projectKey") or project.get("id")
            if not project_key:
                continue
            scope_id = scope_ids.jira_project(project_key)
            scopes.append({
                "scope_id": scope_id,
                "kind": "project",
                "name": project.get("name") or project_key,
                "is_org_wide": False,
                "tenant_id": self.org,
            })

            permission_scheme = None
            scheme_error: Exception | None = None
            try:
                permission_scheme = await self.fetcher.fetch_project_permission_scheme(project_key)
            except Exception as exc:
                # Capture the error; the `if not scheme_id` block below emits a
                # richer warning (with HTTP status) AND records the project in
                # acl_incomplete_projects, superseding a plain inline warning and
                # also covering the "scheme returned but has no id" case.
                scheme_error = exc

            scheme_id = None
            if isinstance(permission_scheme, dict):
                scheme_id = permission_scheme.get("id") or permission_scheme.get("permissionSchemeId")

            if not scheme_id:
                # Could not resolve a permission scheme for this project. This is a
                # fail-CLOSED ACL gap: the scope stays in the artifact with no browse
                # grants, so the query-time gate denies it to EVERYONE. Previously the
                # error was swallowed silently — a 403 on a team-managed project looked
                # identical to a clean run. Surface it loudly and record it so the
                # operator can grant access and re-run discovery (mirrors the
                # missing-seclevel warning in infra/jira/run.py).
                status = getattr(getattr(scheme_error, "response", None), "status_code", None)
                if status is not None:
                    reason = f"permission scheme fetch failed (HTTP {status})"
                elif scheme_error is not None:
                    reason = f"permission scheme fetch failed ({type(scheme_error).__name__}: {scheme_error})"
                else:
                    reason = "no permission scheme returned by Jira"
                logger.warning(
                    "[%s] Project %s ACL INCOMPLETE — %s. The '%s' scope will have NO "
                    "browse grants and will be denied to ALL users by the query-time gate. "
                    "Team-managed projects commonly 403 here; grant the connector admin "
                    "Browse Projects rights and re-run `--stage discovery`.",
                    self.org, project_key, reason, scope_id,
                )
                acl_incomplete_projects.append({
                    "project_key": project_key,
                    "scope_id": scope_id,
                    "reason": reason,
                    "http_status": status,
                })
                continue

            permissions = await self.fetcher.fetch_permission_scheme_permissions(str(scheme_id))
            if isinstance(permissions, dict):
                permissions = permissions.get("permissions") or permissions.get("values") or permissions.get("permissionSchemes") or []
            if not isinstance(permissions, list):
                continue

            project_rule = project_permission_rules.setdefault(project_key, {
                "grant_sources": [],
            })

            for grant in permissions:
                if grant.get("permission") not in ("BROWSE_PROJECTS", None) and grant.get("key") not in ("BROWSE_PROJECTS", None):
                    continue
                holder = grant.get("holder") or grant.get("grantee") or grant.get("principal") or grant
                htype = holder.get("type") or holder.get("holderType") or holder.get("kind")

                if htype == "user":
                    # Jira Cloud permission API returns accountId in "parameter"/"value",
                    # not in an "accountId" key. Try all fallback locations.
                    account_id = (holder.get("accountId") or holder.get("account_id")
                                  or holder.get("parameter") or holder.get("value") or "")
                    account_id = str(account_id).removeprefix("accountId:").strip()
                    if account_id and account_id in principals:
                        row = (scope_id, scope_ids.jira_user(account_id), "jira_browse_user")
                        if row not in seen_scope_rows:
                            seen_scope_rows.add(row)
                            scope_principals.append({
                                "scope_id": scope_id,
                                "principal_id": scope_ids.jira_user(account_id),
                                "principal_type": "user",
                                "grant_source": "jira_browse_user",
                            })
                elif htype == "group":
                    # Jira Cloud returns group name in "parameter" and UUID in "value".
                    # Resolve to canonical jira:G{uuid} using the name→id map built in Phase 1.
                    raw_id = (holder.get("groupId") or holder.get("id") or holder.get("name")
                              or holder.get("value") or holder.get("parameter") or "")
                    resolved = group_name_to_id.get(str(raw_id), str(raw_id)) if raw_id else ""
                    group_id = scope_ids.jira_group(resolved) if resolved else ""
                    if group_id:
                        row = (scope_id, group_id, "jira_browse_group")
                        if row not in seen_scope_rows:
                            seen_scope_rows.add(row)
                            scope_principals.append({
                                "scope_id": scope_id,
                                "principal_id": group_id,
                                "principal_type": "group",
                                "grant_source": "jira_browse_group",
                            })
                elif htype == "projectRole":
                    role_id = (holder.get("id") or holder.get("roleId")
                               or holder.get("parameter") or holder.get("value") or "")
                    if role_id:
                        try:
                            # Correct endpoint: /project/{key}/role/{id}  (not /projects/)
                            actors = await self.fetcher.get(f"/rest/api/3/project/{project_key}/role/{role_id}")
                        except Exception as exc:
                            logger.warning(
                                "[%s] Role %s actor fetch failed for project %s (%s); "
                                "role-based grants will be missing.",
                                self.org, role_id, project_key, exc,
                            )
                            actors = {}
                        actor_list = []
                        if isinstance(actors, dict):
                            actor_list = actors.get("actors") or actors.get("values") or []
                        for actor in actor_list:
                            atype = actor.get("type") or actor.get("actorType") or ""
                            if "user" in atype.lower():
                                # Jira Cloud: accountId lives in actorUser sub-object
                                actor_user = actor.get("actorUser") or {}
                                acct = (actor_user.get("accountId")
                                        or actor.get("accountId") or actor.get("account_id")
                                        or actor.get("name") or "")
                                if acct and acct in principals:
                                    row = (scope_id, scope_ids.jira_user(acct), "jira_browse_role")
                                    if row not in seen_scope_rows:
                                        seen_scope_rows.add(row)
                                        scope_principals.append({
                                            "scope_id": scope_id,
                                            "principal_id": scope_ids.jira_user(acct),
                                            "principal_type": "user",
                                            "grant_source": "jira_browse_role",
                                        })
                            elif "group" in atype.lower():
                                actor_group = actor.get("actorGroup") or {}
                                raw_gid = (actor_group.get("groupId") or actor_group.get("name")
                                           or actor.get("id") or actor.get("groupId") or actor.get("name") or "")
                                resolved_gid = group_name_to_id.get(str(raw_gid), str(raw_gid)) if raw_gid else ""
                                gid = scope_ids.jira_group(resolved_gid) if resolved_gid else ""
                                if gid:
                                    row = (scope_id, gid, "jira_browse_role")
                                    if row not in seen_scope_rows:
                                        seen_scope_rows.add(row)
                                        scope_principals.append({
                                            "scope_id": scope_id,
                                            "principal_id": gid,
                                            "principal_type": "group",
                                            "grant_source": "jira_browse_role",
                                        })
                elif htype == "reporter":
                    if "jira_dynamic_reporter" not in project_rule["grant_sources"]:
                        project_rule["grant_sources"].append("jira_dynamic_reporter")
                elif htype == "assignee":
                    if "jira_dynamic_assignee" not in project_rule["grant_sources"]:
                        project_rule["grant_sources"].append("jira_dynamic_assignee")
                elif htype in ("applicationRole", "authenticated"):
                    scopes[-1]["is_org_wide"] = True

        artifact = {
            "scopes": scopes,
            "principals": [principals[k] | {"principal_id": scope_ids.jira_user(k)} for k in sorted(principals)],
            "scope_principals": scope_principals,
            "groups": [groups[k] | {"group_id": k} for k in sorted(groups)],
            "group_members": group_members,
            "project_permission_rules": project_permission_rules,
            "acl_incomplete_projects": acl_incomplete_projects,
        }
        return await self.phase_3_security_crawl(artifact)

    async def phase_3_security_crawl(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        """Append issue-security level scopes and principal grants to an artifact.

        This is intentionally a separate phase so older test fixtures and
        downstream callers can exercise the security crawl in isolation.
        """
        scopes = artifact.setdefault("scopes", [])
        scope_principals = artifact.setdefault("scope_principals", [])
        principals = {p.get("principal_id", ""): p for p in artifact.get("principals", [])}
        seen_scope_rows: set[tuple[str, str, str]] = {
            (sp.get("scope_id", ""), sp.get("principal_id", ""), sp.get("grant_source", ""))
            for sp in scope_principals
        }

        # Build name->UUID lookup so security-level group grants (which return the
        # group name in the "parameter" field) can be resolved to the same UUID
        # stored in group_members (from Phase 1's /group/bulk response).
        group_name_to_id: dict[str, str] = {}
        for g in artifact.get("groups", []):
            gname = g.get("name", "")
            gid   = g.get("group_id", "")
            if gname and gid:
                group_name_to_id[gname] = gid

        logger.info("[%s] Phase 3: Security crawl starting", self.org)

        try:
            schemes_resp = await self.fetcher.fetch_security_schemes()
        except Exception as exc:
            logger.warning(
                "[%s] Security scheme crawl failed (%s); issue-security-level grants "
                "will be missing — items under security levels may be denied to all users.",
                self.org, exc,
            )
            schemes_resp = None

        schemes = []
        if isinstance(schemes_resp, dict):
            schemes = schemes_resp.get("values") or schemes_resp.get("issueSecuritySchemes") or schemes_resp.get("schemes") or []
        elif isinstance(schemes_resp, list):
            schemes = schemes_resp

        logger.info("[%s] Found %d security scheme(s)", self.org, len(schemes))
        levels_added = 0
        grants_added = 0

        def _resolve_user_account_id(holder: Dict[str, Any]) -> str:
            account_id = holder.get("accountId") or holder.get("account_id")
            if not account_id:
                user = holder.get("user") or {}
                if isinstance(user, dict):
                    account_id = user.get("accountId") or user.get("account_id") or ""
            if not account_id:
                param = holder.get("parameter") or ""
                if isinstance(param, str):
                    # Handles both "accountId:xxx" prefix and bare accountId in parameter
                    account_id = param.removeprefix("accountId:") if param else ""
            if not account_id:
                # Jira Cloud members endpoint returns accountId in "value" field
                account_id = holder.get("value") or ""
            return str(account_id or "")

        def _resolve_group_id(holder: Dict[str, Any]) -> str:
            group_id = holder.get("groupId") or holder.get("id") or holder.get("name")
            if not group_id:
                group = holder.get("group") or {}
                if isinstance(group, dict):
                    group_id = group.get("groupId") or group.get("id") or group.get("name") or ""
            if not group_id:
                param = holder.get("parameter") or ""
                if isinstance(param, str):
                    group_id = param.removeprefix("groupId:") if param.startswith("groupId:") else param
            return str(group_id or "")

        for scheme in schemes:
            scheme_id = scheme.get("id") or scheme.get("issueSecuritySchemeId")
            if not scheme_id:
                continue
            try:
                levels_resp = await self.fetcher.fetch_security_scheme_levels(scheme_id)
            except Exception as exc:
                logger.warning(
                    "[%s] Level fetch failed for security scheme %s (%s); "
                    "levels under it will be missing.",
                    self.org, scheme_id, exc,
                )
                levels_resp = None
            levels = []
            if isinstance(levels_resp, dict):
                levels = levels_resp.get("values") or levels_resp.get("levels") or levels_resp.get("issueSecurityLevels") or []
            elif isinstance(levels_resp, list):
                levels = levels_resp
            logger.info("[%s] Scheme %s: %d level(s)", self.org, scheme_id, len(levels))
            for level in levels:
                level_id = level.get("id") or level.get("securityLevelId")
                if not level_id:
                    continue
                scope_id = scope_ids.jira_seclevel(level_id)
                if not any(s.get("scope_id") == scope_id for s in scopes):
                    scopes.append({
                        "scope_id": scope_id,
                        "kind": "security_level",
                        "name": level.get("name") or level.get("label") or str(level_id),
                        "is_org_wide": False,
                        "tenant_id": self.org,
                    })
                    levels_added += 1
                try:
                    member_resp = await self.fetcher.fetch_security_level_members(str(scheme_id), str(level_id))
                except Exception as exc:
                    logger.warning(
                        "[%s] Member fetch failed for security level %s (%s); this "
                        "level's grants will be empty — items may be denied to all users.",
                        self.org, level_id, exc,
                    )
                    member_resp = None
                members = []
                if isinstance(member_resp, dict):
                    members = member_resp.get("values") or member_resp.get("members") or member_resp.get("issueSecurityLevelMembers") or []
                elif isinstance(member_resp, list):
                    members = member_resp
                for member in members:
                    holder = member.get("holder") if isinstance(member, dict) else None
                    if not isinstance(holder, dict):
                        holder = member if isinstance(member, dict) else {}
                    htype = holder.get("type") or holder.get("holderType") or holder.get("kind")
                    if htype == "user":
                        account_id = _resolve_user_account_id(holder)
                        if account_id and scope_ids.jira_user(account_id) in principals:
                            row = (scope_id, scope_ids.jira_user(account_id), "jira_seclevel")
                            if row not in seen_scope_rows:
                                seen_scope_rows.add(row)
                                scope_principals.append({
                                    "scope_id": scope_id,
                                    "principal_id": scope_ids.jira_user(account_id),
                                    "principal_type": "user",
                                    "grant_source": "jira_seclevel",
                                })
                                grants_added += 1
                    elif htype == "group":
                        group_id = _resolve_group_id(holder)
                        if group_id:
                            # Resolve group name -> jira:G{uuid} so it matches group_members
                            resolved = group_name_to_id.get(group_id, group_id)
                            canonical_id = scope_ids.jira_group(resolved) if resolved else ""
                            if canonical_id:
                                row = (scope_id, canonical_id, "jira_seclevel")
                                if row not in seen_scope_rows:
                                    seen_scope_rows.add(row)
                                    scope_principals.append({
                                        "scope_id": scope_id,
                                        "principal_id": canonical_id,
                                        "principal_type": "group",
                                        "grant_source": "jira_seclevel",
                                    })
                                    grants_added += 1

        logger.info(
            "[%s] Phase 3 complete: %d security level(s), %d seclevel grant(s)",
            self.org, levels_added, grants_added,
        )
        return artifact

    def write_artifact(self, artifact: dict) -> Path:
        out = self.paths.scope_principals
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(out)
        logger.info("Wrote Jira ACL artifact -> %s", out)
        return out


def append_dynamic_issue_scope_rows(artifact: Dict[str, Any], issue: Dict[str, Any]) -> bool:
    """Append dynamic reporter/assignee grants for a Jira issue.

    The permission scheme is discovered once per project in Phase 2.
    Ingest-time application happens here so the JSON artifact includes the
    concrete principals tied to each issue.
    """
    project_key = ((issue.get("fields") or {}).get("project") or {}).get("key") or issue.get("project")
    if not project_key:
        return False

    project_rules = (artifact.get("project_permission_rules") or {}).get(project_key) or {}
    grant_sources = set(project_rules.get("grant_sources", []))
    if not grant_sources:
        return False

    scope_id = scope_ids.jira_project(project_key)
    fields = issue.get("fields") or {}
    reporter_id = (fields.get("reporter") or {}).get("accountId")
    assignee_id = (fields.get("assignee") or {}).get("accountId")

    scope_principals = artifact.setdefault("scope_principals", [])
    seen = {
        (sp.get("scope_id", ""), sp.get("principal_id", ""), sp.get("grant_source", ""))
        for sp in scope_principals
    }

    changed = False
    for account_id, grant_source in ((reporter_id, "jira_dynamic_reporter"), (assignee_id, "jira_dynamic_assignee")):
        if grant_source not in grant_sources or not account_id:
            continue
        principal_id = scope_ids.jira_user(account_id)
        row = (scope_id, principal_id, grant_source)
        if row in seen:
            continue
        seen.add(row)
        scope_principals.append({
            "scope_id": scope_id,
            "principal_id": principal_id,
            "principal_type": "user",
            "grant_source": grant_source,
        })
        changed = True

    return changed


async def run_discovery(org: str, fetcher: Any, output_dir: Path = Path("output")) -> dict:
    discovery = JiraDiscovery(fetcher, org, output_dir=output_dir)
    principals, groups = await discovery.phase_1_identity_crawl()
    artifact = await discovery.phase_2_project_acl_crawl(principals, groups)
    discovery.write_artifact(artifact)
    return artifact
