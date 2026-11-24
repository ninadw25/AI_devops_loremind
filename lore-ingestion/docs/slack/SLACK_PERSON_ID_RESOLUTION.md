# Slack Person-ID Resolution

**Audience:** whoever is building the centralized cross-source ID-resolution system.
**Purpose:** everything Slack gives you for identity, how the **Grid** and **fallback**
pathways differ, which one you'll see most, and exactly what the Slack connector emits
for the resolver to consume.

> Read alongside [`docs/RBAC/Identity_resolution.md`](../RBAC/Identity_resolution.md)
> (the surrogate-`person_id` + alias-table design) and
> [`SLACK_ACL_FOR_RBAC.md`](./SLACK_ACL_FOR_RBAC.md) (ACL/membership). This doc is the
> Slack-specific feed into that resolver.

---

## 0. TL;DR

- Slack's native person key is the **user ID** `U…` (e.g. `U0B8KEP3SKS`). Everything
  hangs off it.
- The connector emits each actor as a **principal** `slack:{user_id}` (via
  `common/scopes.py:slack_user()`), plus name + email when available.
- For the centralized resolver, Slack contributes two alias keys per person:
  `src:slack:{user_id}` and (when email is readable) `email:{address}`. **Email is the
  cross-source join** to Gmail/Jira; the Slack user ID is the stable within-Slack key.
- **Two pathways, auto-selected by token capability** (`infra/slack/capabilities.py`):
  - **Grid (admin/Discovery)** — org-wide directory, deterministic, complete. **~90% of
    customers.** This is the one you optimize for.
  - **Fallback (bot token)** — workspace-scoped, only what the bot can see. The ~10%
    non-Grid case.
- Slack does **not** mint `person_id`. It hands the resolver a `SourceIdentity`; the
  resolver mints/looks up the surrogate `person_id`.

---

## 1. The Slack native identifier

| Thing | Example | Notes |
|-------|---------|-------|
| **User ID** | `U0B8KEP3SKS` | The identity key. Stable for a user within a workspace. |
| **Enterprise user ID** | `W012ABC…` (`W`-prefixed) | On **Grid**, a user has ONE enterprise identity spanning all workspaces. See §5. |
| **Team (workspace) ID** | `T0B7…` | Which workspace. A Grid org has many. |
| **Principal (our format)** | `slack:U0B8KEP3SKS` | `common/scopes.py:slack_user(user_id)`. This is what lands in actors + ACL. |
| **bot_id / app_id** | `B0B7…` / `A0B7…` | App/bot identities — **not persons** (see §7). |

The connector's actor builder is `infra/slack/normalizer.py:build_actor()`:

```python
Actor(
    id          = scopes.slack_user(user_id),   # "slack:U…"
    display_name= profile.display_name | real_name | name | user_id,
    email       = profile.email or None,         # requires users:read.email
    source      = "slack",
    is_bot      = user.is_bot,
    is_deleted  = user.deleted,
)
```

The event itself is keyed separately (`make_slack_id(org, channel, ts)`); **person
identity is the `slack:{user_id}` principal carried inside `actors[]` / `primary_actor`.**

---

## 2. What Slack emits for the resolver — the `SourceIdentity`

Map Slack → the `SourceIdentity` contract the resolver consumes:

```jsonc
{
  "source":     "slack",
  "native_id":  "U0B8KEP3SKS",          // → alias key "src:slack:U0B8KEP3SKS"
  "emails":     ["ravi@ezobooks.in"],    // → alias key "email:ravi@ezobooks.in" (Tier-1b join)
  "names":      ["Ravi Kumar"],          // display_name / real_name — for Tier-2 name match
  "handles":    ["@ravi"],               // display_name handle
  "kind":       "internal_person",       // see §6
  "enterprise_user_id": "W012ABC",       // Grid only — cross-workspace identity (see §5)
  "provenance": {"created_from": "slack:admin.users.list" | "slack:users.list"}
}
```

The resolver then does (from `Identity_resolution.md` §1.3):
1. **Tier 1a:** `src:slack:U0B8KEP3SKS` already in the alias table → return its `person_id`.
2. **Tier 1b:** else join by `email:…` → attach this Slack `native_id` to the existing person.
3. **Tier 2 (hook):** name + corroborator (never auto-merges here).
4. **No match:** mint a new surrogate `person_id`, write both alias keys.

**The alias keys Slack must produce:** `src:slack:{user_id}` (always) and `email:{addr}`
(when `users:read.email` is granted). That's the whole contract.

---

## 3. The two pathways

Both produce the same `SourceIdentity` shape; they differ in **how complete and
authoritative** the directory is. Selected automatically from the token —
`infra/slack/capabilities.py:detect()` → `identity_pathway`:

| `identity_pathway` | When | Source method |
|--------------------|------|---------------|
| `ADMIN_DIRECTORY` | Grid + `admin.users:read` | `admin.users.list` (org-wide) |
| `BOT_PROFILE` | standard token w/ `users:read` | `users.list` (workspace) |
| `HEURISTIC` | no `users:read` | name/handle inference only (last resort) |

### 3a. Grid pathway (primary — ~90%)

Code: `infra/slack/admin.py` (`SlackAdminClient.list_users()`, `build_grid_directory()`)
and `infra/slack/discovery_api.py` (`SlackDiscoveryClient.users_list()`).

- **`admin.users.list`** — every user across **every workspace** in the org, including
  **deactivated** users, guests, and admin/owner flags. Org token, `admin.users:read`.
- **`admin.roles.listAssignments`** — role assignments (admin/owner/custom) →
  `GridDirectory.roles_by_user`. Feeds RBAC, not identity, but arrives on the same pass.
- **`discovery.users.list`** (when Discovery is enabled) — the most complete directory,
  same org-wide coverage, used to corroborate membership for ACL.
- **Why it's authoritative:** you get the *whole* org directory up front, deterministically,
  before reading a single message — so identity is resolved from a complete directory, not
  discovered message-by-message.

### 3b. Fallback pathway (bot token — ~10%)

Code: `infra/slack/fetcher.py:load_user_cache()` → `users.list`, consumed by
`build_actor()`.

- **`users.list`** — members of the **single workspace** the bot is installed in. No
  cross-workspace view, no org roles.
- Email only if `users:read.email` granted; profile fields only with `users.profile:read`.
- **Gaps vs Grid:** can miss users who never posted in channels the bot can read; no
  enterprise user ID; deactivated/guest visibility is partial.

### Which is used most

**Grid.** ~90% of customers are Enterprise Grid (the Grid-primary strategy), so
`ADMIN_DIRECTORY` is the hot path. Build and tune for Grid; keep `BOT_PROFILE`
correct as the graceful fallback. `HEURISTIC` should be rare and is best-effort only.

---

## 4. Field availability by pathway

| Field | Grid (`admin.users.list` / Discovery) | Fallback (`users.list`) | Scope needed |
|-------|---------------------------------------|--------------------------|--------------|
| user_id (`U…`) | ✅ | ✅ | — |
| enterprise_user_id (`W…`) | ✅ | ❌ | Grid |
| display_name / real_name | ✅ | ✅ | `users:read` (+ `users.profile:read` for full profile) |
| **email** | ✅ | ✅ (if scope) | `users:read.email` |
| is_bot | ✅ | ✅ | — |
| deactivated/deleted | ✅ (incl. historical) | partial | — |
| is_admin / is_owner | ✅ | ❌ | `admin.users:read` |
| roles (custom) | ✅ (`admin.roles.listAssignments`) | ❌ | `admin.roles:read` |
| guest / restricted flag | ✅ | partial | Grid |
| title / department (profile) | ✅ | ✅ (if populated) | `users.profile:read` |
| which workspaces | ✅ | single | Grid |

`# ASSUMED` note: the exact `admin.users.list` field names (`is_admin`, `deactivated`,
top-level `email`) are parsed defensively in `admin.py:_user_record()` and must be
confirmed against a real Grid token — flagged in code.

---

## 5. Enterprise Grid identity specifics (read this carefully)

- **One person, many workspaces.** On Grid a human has a single **enterprise user**
  (`W…`). The same person may appear with the **same `U…`** across workspaces (org-managed)
  — but treat the **enterprise user ID as the stable cross-workspace key** when present.
  Recommended alias keys on Grid: `src:slack:{enterprise_user_id}` **and**
  `src:slack:{workspace_user_id}` both → the same `person_id`, so either resolves.
- **Deactivated users still matter.** Historical messages reference people who have since
  left. `admin.users.list` returns them; keep them as `kind: internal_person` with a
  `deactivated` flag (don't drop — content is still attributable).
- **Guests / multi-channel-guests / restricted** users are real people with limited
  visibility → `kind: external_person` (or internal with a guest flag). They affect ACL
  (see ACL doc), not just identity.
- **Slack Connect / external org members** appear with a **different `team_id`** and often
  `#EXT#` markers. `discovery.py:_is_external_principal()` already detects these:
  `team_id != org team`, `is_bot`, `deleted`, or `#EXT#` in email/name → external.

---

## 6. `kind` classification from Slack signals

The resolver's `kind` (`internal_person | external_person | functional | automation`)
should be set from Slack as:

| Slack signal | `kind` |
|--------------|--------|
| `is_bot` true, or has `bot_id`/`app_id` (no human `user`) | `automation` |
| `team_id != org team`, `#EXT#`, or Slack Connect | `external_person` |
| Email is a shared/functional mailbox (`hr@`, `support@`) | `functional` |
| Otherwise a normal member | `internal_person` |

**Never merge across kinds** (a person, the `@jirabot`, and `hr@` are three identities).
Automation/bot identities (the generator bot `U0B8KEP3SKS` in our test data is one) should
generally be **excluded from the person graph** unless explicitly allowlisted as signal.

---

## 7. Bots & apps are not persons

Messages posted by an app carry `bot_id` (`B…`) and `app_id` (`A…`) and a synthetic
`user`. These are **automation**, not people:

- `build_actor()` flags them `is_bot=True`.
- For ACL/identity, route them to `kind: automation` and normally **exclude** from
  `Person` spine nodes.
- The `SLACK_BOT_ALLOWLIST` env var is the escape hatch for bots whose messages ARE
  signal (e.g. a release bot) — see `is_noise()` in `normalizer.py`.

> Gotcha we hit in testing: our test data was posted entirely by one generator bot, so
> the whole dataset collapsed to a single automation identity until the bot was
> allowlisted. Real workspaces have many human authors.

---

## 8. Edge cases & gotchas

- **Slack-only person, no email.** Common (mention-only, or `users:read.email` not
  granted). They have **no `email:` alias** → resolver keys only on `src:slack:U…`, and
  cross-source merge to Gmail/Jira can only happen later via Tier-2 name matching. This is
  expected; don't force-merge.
- **Display name changes.** `display_name` is mutable; **never** key identity on it. Keep
  it in `names[]` for Tier-2 only.
- **Email canonicalization** still applies to Slack emails (case-fold, strip `+tag`, gmail
  dots) before the `email:` join — same `canonicalize_emails()` the resolver uses.
- **Same `U…` reused?** Slack does not recycle user IDs within a workspace, so
  `src:slack:U…` is safe as a permanent alias key.
- **Workspace vs enterprise ID confusion** on Grid — see §5; alias BOTH.
- **`primary_actor = "slack:unknown"`** when a message has no resolvable author
  (`normalizer.py`). Filter these out of identity feed.

---

## 9. What the connector emits today vs what the resolver needs

| Need | Status in code |
|------|----------------|
| `slack:{user_id}` principal on every actor | ✅ `build_actor()` |
| email on actor (when scope) | ✅ `build_actor()` (profile.email) |
| is_bot / is_deleted flags | ✅ |
| Grid org-wide directory w/ roles | ✅ `admin.py:build_grid_directory()` |
| enterprise_user_id surfaced | ⚠️ **TODO** — `admin.users.list` returns it; not yet mapped into the actor/SourceIdentity |
| `kind` classification emitted | ⚠️ **partial** — `is_bot`/external detectable; not yet a first-class field on the actor |
| Surrogate `person_id` minting | ❌ Not Slack's job — the centralized resolver owns this |
| Persistent alias table across runs | ❌ **TODO in the resolver** (today `person_registry.py` is Gmail-only, rebuilt per run) |

**Action items for the centralized resolver** (Slack-facing):
1. Consume `SourceIdentity{source:"slack", native_id, emails, names, kind}` from the
   connector (add a thin adapter over `build_grid_directory()` / `build_actor()`).
2. Write alias keys `src:slack:{user_id}` (+ `src:slack:{enterprise_user_id}` on Grid) and
   `email:{addr}`.
3. Surface `enterprise_user_id` and `kind` from the connector (the two ⚠️ TODOs above).
4. Treat `automation` (bots/apps) and `external_person` (Slack Connect) as non-mergeable
   kinds.
