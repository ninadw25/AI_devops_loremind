# Centralized Identity Resolution — the locked plan

**Status:** design locked, partially built. This is the **simple, authoritative** spec.
Where it conflicts with the older `Identity_resolution.md` (51 KB) or
`Identity_resolution_plan.md`, **this doc wins** — those are kept as background only.

**Goal.** Every human who appears in Gmail, Slack, or Jira collapses to **one stable
`person_id`**, so RBAC at query time is a set intersection and a citation can say "this is
the same person across all sources." Adding GitLab / GitHub / Teams later must require
**one new file and nothing else**.

---

## 1. The one idea

> A person is a **surrogate id**. Every source-native key and every email is an **alias**
> that points at that id. Resolution = "look up the alias; if missing, decide whether to
> attach to an existing person or mint a new one."

Two tables. One resolver function. Sources are pluggable adapters. That's the whole system.

```
   Gmail ─┐
   Slack ─┤── adapter → SourceIdentity ─→ resolve() ─→ person_id
   Jira  ─┘                                   │
   (GitLab/GitHub later, same seam)           ▼
                                   output/identity/{org}_identity.json
                                     persons[]  +  alias_table{}
                                              │
                                              ▼
                            elysia-backend Postgres: person + person_alias
```

---

## 2. Locked decisions (do not relitigate)

1. **`person_id` is an opaque surrogate**, allocated once, never reused, never derived from
   email/name/accountId. Format: `p:{org}:{n}` (zero-padded counter), persisted in the
   artifact so it is **stable across re-runs**. This is the fix for Gmail's `person:{email}`
   (which flips when an email changes) and Jira's two divergent `person_id` paths.
2. **The alias table is the source of truth.** N alias keys → 1 `person_id`. Everything else
   (canonical name, kind) is a derived attribute.
3. **Alias keys reuse the ACL principal string verbatim** — no second namespace.
   This is the one real simplification vs the old doc (which invented `src:slack:…`).
   Because the backend already holds a logged-in user's principals (`slack:U…`, `jira:U…`),
   keying the alias table on the *same* strings makes `principal → person_id` a
   **zero-translation lookup**. The only extra key is `email:{canon}`, the cross-source bridge.

   | Alias key | Emitted by | Example |
   |-----------|-----------|---------|
   | `email:{canon}` | any source with a visible email | `email:ravi@ezobooks.in` |
   | `slack:{user_id}` (+ `slack:{enterprise_id}` on Grid) | Slack | `slack:U0B8KEP3SKS` |
   | `jira:U{accountId}` | Jira | `jira:U712020:af3…` |
   | *(Gmail has no separate key — its native key IS `email:{canon}`)* | Gmail | — |
   | `gitlab:U{id}` / `github:U{node_id}` | **future** | — |

   All formats are owned by `common/scopes.py` (locked by `test_scopes.py`). Email is
   canonicalized by `common/email_canon.py` before becoming a key.
4. **Four kinds, never merge across them:** `internal_person`, `external_person`,
   `functional` (shared mailbox `hr@`), `automation` (bots/apps). A person, `@jirabot`, and
   `hr@` are three identities forever.
5. **Conservative by default.** A wrong merge mis-attributes content (privacy + correctness).
   When unsure → **review queue, not merge**. Fail-closed is the same instinct RBAC uses.

---

## 2.5 Internal vs external — two regimes (read this first)

Internals and externals are resolved by **different machinery**, because only one of them
has an authoritative list.

### Internal employees — a closed, deterministic set

The **internal roster** is the **union of every org directory we can read**, cross-linked
by email:

| Directory source | API | Coverage |
|------------------|-----|----------|
| Gmail / Workspace | Admin SDK `list_users()` | every account in the domain |
| Slack **Grid** (paid) | `admin.users.list` | every member, **every workspace**, org-wide |
| Jira | `users/search` | the site's users |

**A person is `internal_person` iff they appear in ≥1 directory** (equivalently: carry a
verified org-domain email / org `team_id`). Because every directory row has an org email,
**internal↔internal links are 100% deterministic** — pure email join across directories, no
fuzzy matching. The roster is built **first**, before any message actors are resolved, and
it is a *closed set of a few hundred people*.

The **only** fuzziness for internals is Jira's GDPR-hidden email: an email-less Jira actor
that is internal is matched by name **against this closed roster** (Tier 2a, `bounded=True`)
— safe precisely because the candidate set is small and authoritative, not open-world.

Output for internals is auditable: who mapped to all sources, who is missing a Slack/Jira
link, and who is internal-looking but absent from a directory → the review report (§7).

### Externals — open world, conservative

Clients / consultants / vendors are **not in any directory**. They appear only as
participants / reporters. No roster to anchor them, so:

- **Deterministic only** when a shared `email:{canon}` links two sources → auto-join.
- Otherwise **stay source-local**; never name-merged automatically (Tier 2b off by default
  → review queue). A wrong external merge is a privacy leak, so the bias is "don't."

### The classifier gates which path you take

```
person has org-domain email OR is in any directory ?
    ├─ yes → internal_person → deterministic roster path (Tier 1 + bounded Tier 2a)
    └─ no  → external_person → open-world path (Tier 1 email only; Tier 2b deferred)
bot / app / shared-mailbox signal → automation / functional (never either person path)
```

`kind` is set by the adapter from source signals (Slack `is_bot`/`#EXT#`/foreign team,
Jira `accountType`, functional-mailbox prefixes) and **directory membership is the
authoritative override** — if a directory says internal, that wins over weak per-source hints.

---

## 3. The contract — `SourceIdentity` (the modular seam)

Every connector emits a list of these. The resolver consumes only this; it never imports a
connector. **This struct is the entire extension surface.**

```python
@dataclass
class SourceIdentity:
    source:       str            # "gmail" | "slack" | "jira" | "gitlab" | ...
    native_key:   str            # the alias key in §2.3 form, e.g. "slack:U0B8…", "jira:U5d8…"
    emails:       list[str]      # raw; canonicalized → email: keys. MAY BE EMPTY (Jira!).
    names:        list[str]      # display/real names — Tier-2 candidates only, never a key
    kind:         str            # internal_person | external_person | functional | automation
    extra_keys:   list[str]      # e.g. Grid enterprise id "slack:W…"; usually []
    is_internal:  bool           # source's best hint; directory is authoritative
    raw:          dict           # provenance, kept for audit
```

Adapters (one per source, all read **artifacts that already exist** — no new fetching):

| Adapter | Reads | native_key | email present? | feeds roster? |
|---------|-------|-----------|----------------|---------------|
| `from_gmail` | `output/knowledge/{org}_person_registry.json` (Workspace directory) | `email:{canon}` | always | ✅ directory |
| `from_slack` | `output/slack/{org}_scope_principals.json` `principals[]` (Grid `admin.users.list`) | `slack:{user_id}` (+ `slack:{W…}`) | if scope granted | ✅ directory (Grid) |
| `from_jira`  | `output/jira/{org}_scope_principals.json` `principals[]` (`users/search`) | `jira:U{accountId}` | rarely | ✅ directory |
| `from_gitlab` *(future)* | gitlab principals artifact | `gitlab:U{id}` | usually | ✅ if it has a member API |

The **internal roster is the union of every "feeds roster" directory**, cross-linked by
email — not one spine source. A directory entry with an org email is authoritative proof of
internal membership even if another source never saw that person.

> **Adding a source = write one `from_<source>()` that returns `list[SourceIdentity]`, and
> register its key prefix in `common/scopes.py`.** Resolver, tables, artifact: untouched.

---

## 4. The resolver — one function, two tiers do 95 %

```python
def resolve(si: SourceIdentity, store) -> str:
    keys = [si.native_key, *si.extra_keys] + [email_key(e) for e in si.emails]
    keys = [k for k in keys if k]

    # ── skip non-identities ──────────────────────────────────────────────
    if not keys or si.native_key in ("", "slack:unknown", "jira:Uunknown"):
        return None                                  # never mint person:unknown

    # ── Tier 1: alias hit (native key OR email) ──────────────────────────
    hits = {store.alias[k] for k in keys if k in store.alias}
    if len(hits) == 1:
        pid = hits.pop()
        store.bind(keys, pid)                        # attach any new keys
        return pid
    if len(hits) > 1:
        return store.merge(hits, keys)               # shared email proves same human → merge

    # ── Tier 2a: email-less INTERNAL → bounded name match vs closed roster ─
    if si.is_internal and not si.emails:
        pid = bounded_name_match(si, store.internal_roster)   # tier2_scoring, closed set = safe
        if pid:
            store.bind(keys, pid); return pid

    # ── Tier 2b (DEFERRED): open-world external fuzzy match ───────────────
    #   default = DO NOT auto-merge externals. Emit to review queue instead.

    # ── mint ─────────────────────────────────────────────────────────────
    return store.mint(si, keys)
```

- **Tier 1 is deterministic and does almost everything**: native-key hit (re-runs, same
  source) + email join (cross-source). No fuzziness.
- **Tier 2a** is the only fuzzy step and it is **bounded to the internal roster** (a few
  hundred people, a closed set) — this is the safe answer to Jira's hidden email. Uses
  `common/tier2_scoring.py` with `bounded=True`.
- **Tier 2b** (open-world externals) is **off by default**. Externals stay source-local
  until a deterministic signal (shared email) links them, or an operator approves a review-
  queue suggestion. Simple and privacy-safe.

**Process order matters — roster before actors:**

1. **Build the internal roster** from the union of *all* directory crawls (Gmail Admin SDK +
   Slack Grid `admin.users.list` + Jira `users/search`), cross-linked by email. This is the
   complete, deterministic internal set — minted before a single message actor is touched.
2. **Then resolve message actors** against it: internal email-less actors (Jira) Tier-2a
   match into the closed roster; everyone not in the roster and without an org email is
   `external_person` on the open-world path.

Doing directories first is what makes internal resolution deterministic and external
resolution safe — the roster is the closed set Tier-2a matches against.

---

## 5. Per-source specifics the adapters must honor

**Gmail** — native key *is* the email; `from_gmail` reads the existing person_registry
(already does cross-domain `user@a` = `user@b` merging). `is_internal = domain ∈ org_domains`.
Functional mailboxes (`hr@`) → `kind=functional`.

**Slack** — emit `slack:{user_id}`; on Grid also emit `slack:{enterprise_user_id}` in
`extra_keys` so either resolves to one person. `is_bot`/`#EXT#`/foreign `team_id` →
`automation`/`external_person` (never internal). Email only if `users:read.email`.
Display name is mutable → `names` only.

**Jira** — emit `jira:U{accountId}`; **`emails` is usually empty** (GDPR) — that is normal,
not an error. `accountType: app` → `automation`; `customer` → `external_person`. Absorb the
already-minted `person:jira:U{accountId}` / `person:{email}` records (§3.3 of the Jira doc)
via the alias table — don't assume a clean slate. Honor `jira_display_name_overrides` from
org config as a Tier-0 name authority.

---

## 6. Edge cases — and where each is handled

| Edge case | Handled by |
|-----------|-----------|
| Jira email hidden | internal → Tier 2a name match vs roster; external → stays Jira-local |
| Slack-only person, no email | stays slack-local until corroborated; never force-merged |
| Email revealed later (Jira) | id is a surrogate → **never flips**; email just adds an alias |
| Same human, two Jira sites | two `jira:U…` keys → one person (joined by email or roster) |
| "Ravi Kumar" ×3 (name collision) | name alone never merges (Tier 1 needs key/email; 2a needs corroborator) |
| Deleted / sparse actor (id only) | key on the id, sparse person, fine |
| `unknown` / null actor | skipped — never mint `person:unknown` |
| Two persons later proven same | `store.merge()` → `merged_into` redirect, lowest id wins |
| Re-run | persistent alias table → `person_id`s stable, output idempotent |
| Bot / `hr@` mistaken for a person | `kind` veto — never merges into a human |

---

## 7. The deliverable

```
output/identity/{org}_identity.json        persons[] + alias_table{}  →  Postgres person / person_alias
output/identity/{org}_identity_review.json  unmapped buckets + Tier-2b suggestions for an operator
```

`person` row: `{person_id, canonical_name, kind, is_internal, emails[], sources{gmail,slack,jira}, merged_into}`
`person_alias`: `{alias_key → person_id}` — loaded verbatim into Postgres; the backend's
`principal → person_id` join is a primary-key lookup on this table.

---

## 8. What exists vs. what to build

| Piece | Status |
|-------|--------|
| `common/email_canon.py` — canonical email → `email:` key (junk-stripping hardened) | ✅ built |
| `common/tier2_scoring.py` — bounded/open fuzzy scorer; `allow_internal` for Tier 2a | ✅ built |
| `common/scopes.py` — principal/alias key formats | ✅ owns slack/jira; add `gitlab/github` when needed |
| `pipeline/identity/source_identity.py` — `SourceIdentity` + `from_gmail/slack/jira` | ✅ built — the modular seam |
| `pipeline/identity/resolver.py` — `IdentityStore` (`mint/bind/merge`) + tiered `resolve()` | ✅ built — §4 |
| `pipeline/identity/resolve.py` — CLI stage → writes the §7 artifacts | ✅ built |
| `pipeline/tests/test_identity_resolver.py` — golden-fixture correctness + re-run stability | ✅ built (9-case fixture, green) |
| `pipeline/identity/internal_mapping.py` — original Tier-1 internal map | ⚠️ superseded by the resolver; keep until backend cuts over |
| elysia-backend: load artifact → `person`/`person_alias`, query-time join | ❌ backend, separate |

**Validation status (real data):** plumbing proven on EZO Gmail (13.5k identities resolved,
kind-classified, no errors) and on techtest Slack+Jira (empty-principal artifacts handled
gracefully). Merge correctness proven on the golden fixture (Tier-1b email join, Tier-2a
bounded name match, merge()+tombstone, same-name-no-merge veto, bot/functional isolation,
Tier-2b suggestion-not-merge, re-run stability). **Untested until one org has all three
real sources:** live tri-source merge volume/precision — covered structurally by the fixture.
