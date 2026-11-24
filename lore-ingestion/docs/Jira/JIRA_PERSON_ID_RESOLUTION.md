# Jira Person ID Resolution

> **Scope.** This is the Jira-specific companion to
> [`docs/RBAC/Identity_resolution.md`](../RBAC/Identity_resolution.md). That document
> defines the *cross-source* identity model (surrogate `person_id`, alias table,
> `merged_into` redirects, the `SourceIdentity` contract). This one answers a narrower,
> concrete question:
>
> **How does Jira give us a unique ID for every person, where does it appear in the
> payloads, how does this connector capture it, and what does the connector already do
> with it today — so the centralized resolver can join Jira identities to Gmail/Slack
> ones without re-inventing what's already wired?**
>
> Everything below is grounded in the as-built connector (`infra/jira/`) and pipeline
> (`pipeline/`) with `file:line` citations. Where behavior is *designed but not yet wired*,
> it is flagged **[PLANNED]**. Where the connector already does something the resolver must
> reconcile (not start from scratch), it is flagged **[AS-BUILT]**.

---

## 0. TL;DR — the two things to know

**1. `accountId` is the only stable, unique person key in Jira Cloud.** It is an opaque
string (e.g. `5d8f1a2b3c4d5e6f7a8b9c0d` or `712020:af3...`), globally unique within an
Atlassian *site*, assigned once and never reused. Everything else Jira shows you about a
person — `displayName`, `emailAddress`, the legacy `name`/`key`/`username` — is either
display-only, privacy-gated, mutable, or deprecated.

This connector turns every `accountId` into the canonical principal string:

```
jira:U{accountId}          # e.g. jira:U5d8f1a2b3c4d5e6f7a8b9c0d
```

minted by `common/scopes.py::jira_user()` (`common/scopes.py:59`). **Note: no colon after
`U`.** That string is what lands in Neo4j and in the ACL artifact, and it is the
`native_id` the centralized resolver must key on.

**2. Jira identity is ALREADY partly wired into `person_id` today — and inconsistently.**
Unlike what an older draft of this doc assumed, `pipeline/person_registry.py` is **not**
Gmail-only anymore. There are **two** code paths that mint Jira `person_id`s, and they key
them **differently**. The resolver's job is not "add Jira from scratch" — it is "collapse
two divergent as-built paths into one deterministic mapping." See §3.

**The single biggest gotcha:** in Jira Cloud, `emailAddress` is **hidden by default** (GDPR
privacy controls). So the email-based join (Tier 1b in `Identity_resolution.md §4`)
frequently *cannot fire for Jira* — you will have an `accountId` and a `displayName` but no
email. Plan for email-less identity.

---

## 1. Jira's identity primitives

### 1.1 `accountId` — the canonical key

| Property | Value |
|----------|-------|
| **Uniqueness** | Globally unique within an Atlassian *site* (one cloud tenant). |
| **Stability** | Assigned once at account creation; never changes, never reused. |
| **Format** | Opaque string. Two shapes in the wild: legacy hex (`5d8f…`) and newer prefixed (`712020:uuid`). **Treat as opaque — never parse it.** |
| **Mutability** | Immutable. (Contrast `displayName`/`emailAddress`, which a user can change.) |
| **Scope** | Site-scoped. The *same human* on two different Atlassian sites has two different `accountId`s. |

This is the post-GDPR Atlassian identity. Since 2019 the old `username`/`userKey`/`name`
fields are deprecated and absent from Cloud REST v3 responses. **Do not key identity on
anything but `accountId`.**

### 1.2 `accountType` — person vs machine

Jira tags every account with a type. The connector captures it (`infra/jira/discovery.py:49`):

| `accountType` | Meaning | Maps to `kind` (resolver) |
|---------------|---------|---------------------------|
| `atlassian` | A real human with an Atlassian account | `internal_person` / `external_person` |
| `app` | An app / automation / bot integration | `automation` |
| `customer` | A Jira Service Management portal-only customer | `external_person` |

Use this to keep bots and automation out of person-merge heuristics
(`Identity_resolution.md §2.4`). **[AS-BUILT]** the connector already excludes
`accountType == "app"` from person augmentation (`person_registry.py:652`).

### 1.3 `emailAddress` — present *only if visible*

`emailAddress` appears in user payloads **only when the connector's account has permission
to see it AND the target user has not hidden it.** In a default Jira Cloud site it is
absent for most users. When present it is the strongest cross-source join key (matches
Gmail/Slack profile emails); when absent, the resolver must fall back to directory seeding
or bounded name matching.

Discovery reads it defensively and tolerates absence (`infra/jira/discovery.py:47`):

```python
"email": user.get("emailAddress") or user.get("email") or "",
```

### 1.4 `displayName` — display only, never a key

Mutable, non-unique ("Ravi Kumar" may be three different people). Captured for display and
as a Tier-2 name-match candidate, **never** as an identity key. **[AS-BUILT]** the connector
already (a) warns when a display name *looks like a username handle* and (b) supports an
override — see §4.6.

### 1.5 Groups: `groupId` (UUID) vs `name`

Groups are not persons, but they carry identity-resolution hazards because group grants
feed ACLs. The canonical key is **`groupId` (a UUID)**, not the human-readable `name`.
Critically, **Jira Cloud's permission/security APIs return the group *name* in the
`parameter` field and the *UUID* in `value`** — so the connector keeps a name→UUID map to
normalize every group reference to one canonical `jira:G{uuid}` form
(`infra/jira/discovery.py:162`, `:278`, `:329`). See §4.3.

---

## 2. Where `accountId` appears in each payload

When you build the centralized resolver, these are every place a Jira `accountId` surfaces
and where this connector reads it:

| Payload location | Field path | Captured at |
|------------------|-----------|-------------|
| **Issue reporter** | `fields.reporter.accountId` | `normalizer.py:210` (`primary_actor`), `:19-30` (`_build_actors`) |
| **Issue assignee** | `fields.assignee.accountId` | `normalizer.py:19-30` (`_build_actors`, deduped against reporter) |
| **Comment author** | `comment.author.accountId` | `normalizer.py:92` (falls back to `"unknown"`) |
| **Changelog author** | `history.author.accountId` | `normalizer.py:149` |
| **Attachment uploader** | `attachment.author.accountId` | `normalizer.py:247` (falls back to `displayName`) |
| **User directory** | `users/search[].accountId` | `discovery.py:42` |
| **Group member** | `group/member[].accountId` | `discovery.py:132` |
| **Permission grant (user holder)** | `holder.parameter` / `holder.value` ⚠️ | `discovery.py:260-262` |
| **Project-role actor** | `actor.actorUser.accountId` | `discovery.py:311-313` |
| **Security-level member** | holder `accountId` / `parameter` / `value` | `discovery.py:408-419` |

Two of these are subtle and bite people building resolvers:

1. **Permission grants do not use an `accountId` key.** Jira Cloud returns the accountId in
   `parameter` or `value`, sometimes prefixed with `accountId:`. The connector tries every
   location and strips the prefix (`discovery.py:260-262`):

   ```python
   account_id = (holder.get("accountId") or holder.get("account_id")
                 or holder.get("parameter") or holder.get("value") or "")
   account_id = str(account_id).removeprefix("accountId:").strip()
   ```

2. **Project-role actors nest the accountId** inside an `actorUser` sub-object
   (`discovery.py:311-313`), not at the top level.

### 2.1 Actor dedup in the normalizer

`_build_actors` returns `[reporter, assignee]` deduped **by `accountId`** — so a self-
assigned issue yields one actor, not two (`normalizer.py:19-30`). This is the *only*
identity normalization the normalizer does today; it keeps the raw Jira actor dict
(including `accountId`, `displayName`, and `emailAddress` if present) intact in
`event["actors"]` for a downstream resolver to consume.

---

## 3. What the connector produces today — AS-BUILT (read this before designing)

> ⚠️ **Correction vs. earlier drafts.** A previous version of this doc stated that
> `person_registry.py` is "Gmail-only" and that "Jira actors currently do not get a
> unified `person_id`." **That is no longer true on this branch.** Jira identities *are*
> minted into `person_id`s — by **two** different code paths that key them differently.
> The resolver must reconcile these, not start clean.

### 3.1 Principals (ACL side)

Discovery mints `jira:U{accountId}` for every grantee and group member and writes them into
`{org}_scope_principals.json` (the `artifact["principals"]` array built in
`discovery.py::phase_2_project_acl_crawl`). Each principal record carries `email`,
`display_name`, and `account_type` alongside the principal_id (`discovery.py:45-50`).

### 3.2 Actors (content side)

The normalizer attaches raw Jira actor dicts to each `KnowledgeEvent` and sets
`primary_actor` to the bare `accountId` (`normalizer.py:210`, `:92`, `:149`).

### 3.3 `person_id` minting — TWO divergent paths **[AS-BUILT]**

Gmail's baseline is email-keyed (`person_registry.py:353`):

```python
person_id = f"person:{primary_email}"        # e.g. person:jane@acme.com
```

On top of that, **two** places fold Jira principals into the registry — and they key the
Jira person record **differently**:

| Path | Where | Runs during | Keying rule | `source` tag |
|------|-------|-------------|-------------|--------------|
| **A** | `person_registry.augment_registry_from_jira()` (`person_registry.py:611`, called from the CLI main `:794`) | `python -m pipeline.person_registry` | `person:{email}` if email **else** `person:{principal_id}` (=`person:jira:U{accountId}`) (`:687`) | `jira_discovery` |
| **B** | `grouping.load_person_registry()` inline fallback (`pipeline/extraction/grouping.py`) | extraction load (Stage 5) | always `person:{principal_id}` (=`person:jira:U{accountId}`) for email-less principals | `jira_discovery_fallback` |

Both paths register the same lookup keys into `email_to_person_id`: the principal id
(`jira:U…`) **and** the bare `accountId` (`person_registry.py:711-715`).

Path A also does the **cross-source link**: if a principal's email already exists in the
registry (a Gmail person), it appends the Jira `display_name` to that person's `all_names`
(and fixes a garbage `canonical_name`) instead of minting a new node
(`person_registry.py:673-684`). **This rarely fires, because email is usually hidden.**

### 3.4 The two hazards the resolver MUST fix

1. **Path divergence.** With email hidden, both paths land on `person:jira:U{accountId}`,
   so they agree *today*. But the moment email visibility is enabled, Path A switches to
   `person:{email}` while Path B stays `person:jira:U{accountId}` → **two different
   `person_id`s for the same human.** Email-derived `person_id` is unstable by construction.
2. **Runner divergence.** `runners/jira_smoke.py` calls `build_person_registry` +
   `merge_by_name` + `save_registry` **only** — it does **not** call
   `augment_registry_from_jira` (`runners/jira_smoke.py:62-68`). So the **Jira smoke/run
   pipeline gets its identity enrichment solely from Path B**, while the standalone
   `person_registry` CLI uses Path A. Identity keying is therefore *path-dependent*.

**Resolver implication:** the alias table must key Jira humans on `accountId` (stable),
treat email as an attribute to *attach later*, and **absorb the already-minted
`person:jira:U{accountId}` / `person:{email}` records** via `merged_into` redirects rather
than assuming a clean slate.

### 3.5 The `SourceIdentity` mapping for Jira **[PLANNED]**

`SourceIdentity` (`Identity_resolution.md §2.1`) is a *design contract* — it does **not**
exist in code yet (`grep -rn SourceIdentity` returns nothing; verified). When the Jira
normalizer is upgraded to emit it, the mapping is:

```python
SourceIdentity(
    source        = "jira",
    native_id     = actor["accountId"],          # THE key — §1.1
    emails        = [actor["emailAddress"]] if actor.get("emailAddress") else [],
    display_names = [actor["displayName"]] if actor.get("displayName") else [],
    handles       = [],                           # Jira Cloud has no stable @-handle
    is_internal   = (actor.get("accountType") == "atlassian"),   # weak signal; directory is authoritative
    raw           = actor,
)
```

Notes for the implementer:

- `native_id` is **always** `accountId`. Never `displayName`, never `emailAddress`.
- `emails` is **frequently empty** (§1.3). Do not treat an empty email list as an error.
- `handles` is empty: Jira Cloud has no stable public @-handle the way Slack does. Leave it
  empty rather than inventing one from the display name.
- `is_internal` from `accountType` is a *weak* hint. The authoritative internal/external
  split is directory seeding (`Identity_resolution.md §3`) — a Jira directory crawl already
  exists in `discovery.py::phase_1_identity_crawl`.

---

## 4. Building the centralized resolver: Jira-specific rules

### 4.1 The native-id key

The resolver's Tier 1a key for a Jira actor is:

```
src:jira:{accountId}        # e.g. src:jira:5d8f1a2b3c4d5e6f7a8b9c0d
```

(`Identity_resolution.md §1.2`). This is distinct from the *principal* string
`jira:U{accountId}` used in ACLs, and from the *current as-built* graph id
`person:jira:U{accountId}` (§3.3) — same `accountId`, three namespaces. Keep them straight:

| Purpose | Format | Owner |
|---------|--------|-------|
| Alias-table identity key | `src:jira:{accountId}` | resolver (`Identity_resolution.md §1.2`) **[PLANNED]** |
| ACL principal | `jira:U{accountId}` | `common/scopes.py::jira_user` |
| Current graph `person_id` (Jira-only) | `person:jira:U{accountId}` | `person_registry` / `grouping` **[AS-BUILT]** |

The resolver should map `src:jira:{accountId}` → surrogate `person_id`, and redirect the
existing `person:jira:U{accountId}` node to it via `merged_into`.

### 4.2 Email join is unreliable for Jira — design around it

Because `emailAddress` is privacy-gated (§1.3), **Tier 1b (email join) will silently no-op
for most Jira-only actors.** Concretely:

- **Internal employees** → resolve via **directory seeding** instead. The Workspace
  directory already created their `Person` records keyed by corporate email; the Jira
  directory crawl (`discovery.py::phase_1_identity_crawl`) gives you `accountId` +
  (sometimes) the same email. When the email *is* visible and matches, Tier 1b joins them.
  When it isn't, fall back to **bounded name matching against the internal roster** (Tier 2a,
  `Identity_resolution.md §3.4`, `§5.2`) — a closed set of ~hundreds, far safer than
  open-world matching.
- **External reporters / customers** (`accountType` `customer`) → usually email-less and
  not in any directory. They flow through the normal resolver and may simply stay as a
  Jira-only `external_person` until Tier 2b finds a corroborated cross-source match.

**Do not** invent a synthetic email from `accountId@…` to force a join — that fabricates an
identity key and will collide or mis-merge.

### 4.3 Groups: always normalize to the UUID

When a *group* is the grantee (permission or security-level holder), resolve the group
**name → UUID** before keying anything, because `group_members` is stored under the UUID
(from the Phase-1 `/group/bulk` response) while permission APIs report the name in
`parameter`:

```python
# discovery.py:162 builds the map once:
group_name_to_id = {g["name"]: gid for gid, g in groups.items() if g.get("name")}
# …then every group grant is normalized through it (discovery.py:278, :329, :504)
resolved = group_name_to_id.get(str(raw_id), str(raw_id))
group_id = scopes.jira_group(resolved)           # -> jira:G{uuid}
```

`jira_group()` is idempotent and prefix-safe (`common/scopes.py:63-67`). **Note: no colon
after `G`.** If you skip the name→UUID step, group grants won't line up with group members
and people will silently lose access.

### 4.4 Deleted / unknown / null actors

- A comment with no resolvable author becomes `primary_actor = "unknown"`
  (`normalizer.py:92`). The resolver should treat `"unknown"` (and any empty/null
  `accountId`) as **no actor** — skip it, never mint a `person:unknown`.
- Deleted Jira users may still appear in historical issues with their `accountId` but no
  `displayName`/`emailAddress`. The `accountId` is still valid and still unique — key on it
  as normal; the person is just sparse.

### 4.5 Multi-site caveat

`accountId` is unique within one Atlassian *site*. If Loremind ever ingests two Jira sites
for one org, the *same human* will have *two* `accountId`s, and the alias table will
correctly hold two `src:jira:*` keys → one `person_id` (joined by email or Tier 2). This is
fine by design (alias table is N keys → 1 person), but don't assume one human = one Jira
`accountId` across sites.

### 4.6 Reuse the existing override + warning machinery **[AS-BUILT]**

The connector already ships the two things the resolver needs for the username-handle
problem (`jira-problems.md` #1):

- **`jira_display_name_overrides`** in `output/config/{org}_config.json`, keyed by **bare
  accountId** (no `jira:U`): `{ "712020:ae04…": "Real Name" }`. Applied in
  `augment_registry_from_jira` before keying (`person_registry.py:643`, `:660-662`).
- **Username-handle warning**: `_looks_like_username(display_name)` logs a warning telling
  the operator to add an override (`person_registry.py:663-668`).

The resolver should consume the override map as a Tier-0 name authority rather than
re-implementing it.

---

## 5. Quick reference

### 5.1 Identity field cheat-sheet

| Jira field | Unique? | Stable? | Use as identity key? |
|------------|---------|---------|----------------------|
| `accountId` | ✅ (per site) | ✅ immutable | ✅ **yes — the only key** |
| `emailAddress` | ✅ when present | ⚠️ user can change | join signal only, often absent |
| `displayName` | ❌ | ❌ | ❌ never (Tier-2 name candidate only) |
| `name` / `key` / `username` | — | — | ❌ deprecated, absent in Cloud v3 |
| group `groupId` | ✅ | ✅ | ✅ for groups (normalize name→UUID) |
| group `name` | ❌ | ⚠️ | ❌ display only |

### 5.2 ID format ownership (locked strings)

| String | Built by | Example | Status |
|--------|----------|---------|--------|
| `jira:U{accountId}` | `common/scopes.py::jira_user` | `jira:U5d8f…` | AS-BUILT |
| `jira:G{groupId}` | `common/scopes.py::jira_group` | `jira:G6a2c…` | AS-BUILT |
| `person:jira:U{accountId}` | `person_registry` / `grouping` | `person:jira:U5d8f…` | AS-BUILT (resolver must absorb) |
| `person:{email}` | `person_registry` (Jira w/ visible email) | `person:jane@acme.com` | AS-BUILT (unstable key) |
| `src:jira:{accountId}` | resolver alias key | `src:jira:5d8f…` | **[PLANNED]** |

These are persisted in Neo4j and the ACL artifact — changing them is a breaking data
migration, locked by `pipeline/tests/test_scopes.py` (`CLAUDE.md`, `common/scopes.py:16`).

### 5.3 Resolution decision tree for a Jira actor

```
Jira actor dict
  │
  ├─ accountId missing / "unknown"?  ──► skip (not a person)
  │
  ├─ accountType == "app"?           ──► kind = automation, do not person-merge
  │
  ├─ src:jira:{accountId} in alias?  ──► Tier 1a hit: return existing person_id
  │
  ├─ existing person:jira:U{accountId} / person:{email} node?
  │                                  ──► absorb via merged_into into the surrogate person_id
  │
  ├─ emailAddress visible & in alias?──► Tier 1b hit: attach accountId, return person_id
  │
  ├─ internal (directory / roster)?  ──► Tier 2a bounded name match (closed set, safe)
  │
  └─ else                            ──► mint Jira-only external_person;
                                          Tier 2b may merge later with corroborators
```

---

## 7. Redesign delta (P5) — emit identities, don't mint `person_id`

> **Status:** DESIGN. Part of the Jira pipeline redesign
> (`JIRA_PIPELINE_REDESIGN.md`, problem **P5**). This section states the *target*
> behaviour for the Jira-native lane and supersedes, for Jira, the two divergent
> `person_id`-minting paths documented in §3.3.

### 7.1 The position

Under the redesign, **the Jira pipeline does not mint `person_id`s.** Its entire
identity job is to **emit a clean list of identity facts** — `accountId →
display_name(s), email?, account_type` — as a dedicated artifact, and let the
**central resolver** (`pipeline/identity/`, `docs/RBAC/CENTRALIZED_IDENTITY.md`)
own surrogate `person_id` assignment and cross-source joining.

This is exactly the product owner's framing: *"identity resolution is very
simple — it's just giving the list of names connected to the Jira IDs."*

### 7.2 Why this also fixes the §3.4 hazards

The two divergent paths — Path A (`augment_registry_from_jira`) and Path B
(`grouping.load_person_registry` fallback) — exist because the **Gmail-shaped**
pipeline expected every source to fold into the email-keyed person registry.
They key the same human differently and are *path-dependent* (§3.4). In the
Jira-native lane we **stop using both for Jira**:

- No `person:{email}` vs `person:jira:U{accountId}` divergence, because Jira mints
  neither — it emits `accountId` facts and the resolver assigns one `person_id`.
- No runner divergence (`jira_smoke.py` vs the CLI), because there is one
  emission step, not two enrichment paths.

The hazard is removed at the source instead of reconciled downstream.

### 7.3 The emission contract

**Artifact:** the identity facts the resolver consumes. Most of this **already
exists** — `output/jira/{org}_scope_principals.json → principals[]` carries
`{principal_id, email, display_name, account_type}` for every ACL-relevant user
(loremind: 18 principals). The only gap: **content actors** (comment authors,
reporters, changelog authors) who appear in events but may not hold an ACL grant.

So emission = **union of**:
1. `scope_principals.json → principals[]` (ACL-known users), and
2. distinct `accountId`s from event `actors[]` / `primary_actor` across the raw
   events (content-known users), carrying their `displayName` (+ `emailAddress`
   if visible).

Each emitted record is the `SourceIdentity` shape already specified in §3.5:

```python
SourceIdentity(
    source="jira", native_id=accountId,
    emails=[email] if email else [],
    display_names=[displayName] if displayName else [],
    handles=[], is_internal=(account_type == "atlassian"), raw=actor,
)
```

The resolver keys these on `src:jira:{accountId}` (§4.1), applies the decision
tree (§5.3), and **absorbs** any already-present `person:jira:U{accountId}` /
`person:{email}` nodes via `merged_into` (§3.4 resolver implication).

### 7.4 What stays unchanged

- `accountId` is still the only key (§1). `account_type` still excludes `app`
  bots (§1.2). Email is still frequently absent (§1.3) — emit empty, don't
  fabricate (§4.2).
- The override map + username-handle warning (§4.6) still apply — the resolver
  consumes them as a Tier-0 name authority.
- The ACL principal string `jira:U{accountId}` is unaffected; only the **content
  `person_id`** ownership moves from the Jira pipeline to the central resolver.

### 7.5 Build note

Emission is a small, deterministic step (no LLM): walk principals + event actors,
dedupe by `accountId`, write the `SourceIdentity` list. It runs against the real
`output/raw/loremind_jira_2026-06-02.jsonl` + `loremind_scope_principals.json`
immediately. Assert: every event `accountId` appears in the emitted list; `app`
accounts are tagged `automation`; no record is keyed on anything but `accountId`.

---

## 6. See also

- [`docs/RBAC/Identity_resolution.md`](../RBAC/Identity_resolution.md) — the cross-source
  identity model this Jira mapping plugs into (surrogate IDs, alias table, Tiers 1–2,
  merge/undo).
- [`docs/Jira/JIRA_ACL_FOR_RBAC.md`](JIRA_ACL_FOR_RBAC.md) — how `jira:U…` / `jira:G…`
  principals are granted scope access (the *authorization* side; this doc is the
  *identity* side).
- [`docs/jira-problems.md`](../jira-problems.md) — #1 username-handle leak, #2 cross-source
  link blocked by hidden email, #3 `merge_by_name` false-merge risk.
- `common/scopes.py` — the single owner of all `jira:U` / `jira:G` / `jira:project:` /
  `jira:seclevel:` format strings.
- `pipeline/person_registry.py` — `build_person_registry`, `merge_by_name`,
  `augment_registry_from_jira` (Path A).
- `pipeline/extraction/grouping.py` — `load_person_registry` Jira fallback (Path B).
- `infra/jira/discovery.py` — `accountId` capture across directory, groups, permissions,
  and security levels.
- `infra/jira/normalizer.py` — `accountId` capture across issue/comment/changelog actors.
