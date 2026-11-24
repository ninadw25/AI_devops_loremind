# Code Intelligence System — Replacement Requirements

**Status:** requirements only — no design, no implementation yet
**Supersedes:** `chronograph/` (GitLab/GitHub code intelligence pipeline)
**Decision (2026-07-15):** chronograph does not meet the bar for multi-tenant
production use and will be rebuilt from scratch rather than patched. This doc
defines *what the replacement must satisfy*, not how to build it — that's a
separate design doc once these requirements are agreed.
**Why now:** an isolation audit (see `ROADMAP_PROD_READINESS.md` B1/G2)
found chronograph has zero ACL model (no `scope_ids` anywhere in the
codebase), a flat single-tenant DB connection, and a `LIMIT 1` hack that
makes only one repo per org reachable at all. These aren't bugs to patch
individually — the system was never designed with multi-tenancy or access
control as a constraint, unlike every other source in this repo.

---

## 0. The bar to clear

Every other source in lore-ingestion (Gmail, Slack, Jira, Outlook, Teams)
already satisfies these four properties. The code-intel replacement is not
done until it does too — it is not a "code" special case, it's the same
contract every source honors:

1. **Fail-closed ACL.** A node with no scope is invisible to everyone.
   (`D-INGEST-001`, already a locked invariant in this repo.)
2. **Org-agnostic.** No source-specific or org-specific logic in code —
   everything is config/registry-driven.
3. **Deterministic, idempotent identity.** Re-ingesting the same data
   produces the same node IDs and doesn't duplicate or corrupt history.
4. **Per-org tenant isolation**, resolved from a connection registry at
   request time — never a flat shared DSN.

---

## 1. ACL / scope requirements

This is the biggest gap and the reason the rebuild is happening. The
target model already exists and is **locked** for every other source —
see `docs/RBAC/SCOPE_FILTER_HANDOFF.md` (`scope_ids` OR + `scope_all_ids` AND;
background in the historical `docs/ACL_CAPTURE_PLAN.md`). The code-intel system must plug into the
*same* two-store scope model, not invent a parallel one:

- **Scope identity is immutable provenance, captured at ingestion.** Every
  commit, symbol, PR/MR, and issue must carry a `scope_id` (and
  `extra_scope_ids` for multi-scope cases) the same way a Slack message
  carries `slack:channel:{id}` or a Jira issue carries `jira:project:{key}`.
  For code hosts the natural scope unit is **the repository** (mirrors
  Slack's channel-membership shape, not Gmail's participant shape — a repo
  has a fixed member/collaborator list, not a per-thread participant set).
- **Scope membership is mutable policy, re-synced each crawl**, stored in
  Postgres (`scopes` / `principals` / `scope_principals` / `groups` /
  `group_members` — the same tables the other sources already write to).
  Repo collaborators/members ARE the ACL — capture them as principals with
  `grant_source = '{provider}_repo_membership'`, the same pattern Slack
  uses for `slack_channel_membership`.
- **Visibility must be a first-class signal, not decoration.** Today
  `repos.data.visibility` (public/private) is stored but never checked
  anywhere. The replacement must resolve visibility → principal set at
  ingestion time (public repo → org-wide/workspace scope, like a public
  Slack channel; private repo → only its actual collaborator list) and
  gate every read on it.
- **Sub-repo visibility must be representable**, not assumed away. GitHub/
  GitLab support protected branches, private forks, and MR/PR-level
  restricted visibility. Model these the same way Jira's issue security
  levels are modeled today: a primary `scope_id` (the repo) plus an
  `extra_scope_ids` entry for the narrower grant, with "user must satisfy
  ALL scopes" as the visibility rule (§2.5 of `ACL_CAPTURE_PLAN.md`).
- **PRs/MRs, issues, and review comments are ACL-bearing entities too**,
  not just commits/symbols. A private review comment on a restricted PR
  needs its own scope resolution, same as a Jira comment with narrowed
  visibility.
- **No ACL, no query.** The replacement's query layer must apply an
  allow-set check equivalent to `domain/scopes.py:scope_gate()` on lore-
  engine's side — every read, not just the top-level "which org's DB" hop.
  Org-level DB isolation alone (what chronograph got half-built into via
  B1) is necessary but not sufficient — it stops cross-org leaks, not
  cross-repo-within-an-org leaks.

## 2. Commit / history requirements

- **Full history, not a snapshot.** The system must retain the complete
  commit chain per repo (author, message, timestamp, diff stats,
  classification), not just current-state file contents — this is what
  makes "who touched this and when" queries possible, and it's the core
  value chronograph exists to provide.
- **Deterministic node IDs**, routed through the same ID discipline as
  every other node in this repo (`common/hashing.py` pattern) — a commit,
  a symbol, a PR, and a repo must each have a stable ID derivable from
  immutable source facts (repo + sha, repo + qualified_name, etc.), not an
  autoincrement or a re-derived-differently-each-run value. This is what
  makes re-ingestion idempotent and what let the old system's edge dedup
  work — keep it.
- **Idempotent, resumable incremental sync.** Re-polling the same
  `(repo, since)` window must produce the same result. Checkpoint per
  repo per org (not one global checkpoint — today's single
  `chronograph_gitlab_checkpoint.json` is itself a design smell:
  it can't tell two orgs' repos apart). Must survive process restarts
  mid-backfill without re-doing completed work or silently dropping
  incomplete work (today's silent-401-but-checkpoint-still-advances
  behavior is exactly the failure mode to design out).
- **Commit → author identity must resolve through the shared identity
  layer**, not a standalone `dev:{email}` namespace. If a person commits
  code and also discusses it in Slack/email, that must be the *same*
  person node (cross-source identity resolution, `pipeline/person_registry.py`
  already does this for Gmail — code-intel authors need to join it, not
  fork a parallel identity system).

## 3. Org-agnostic / multi-tenancy requirements

- **No hardcoded org, project, or "first repo" assumptions anywhere.**
  The current `LIMIT 1 FROM repos` hack that silently binds an entire
  org's GitLab routes to one arbitrary repo is the concrete failure mode
  to design out from day one — every request must explicitly resolve
  (org, repo) and every org must support an arbitrary number of repos
  across an arbitrary number of providers.
- **Per-org DB connection resolved from the connection registry**
  (elysia-rbac's `org_connection` / `RegistryService`, the same mechanism
  `lore-engine`'s `tenant_registry.py` already uses for Neo4j/memory), not
  a flat `DATABASE_URL`. Fail-closed: if routing is enabled and an org has
  no registered connection, the system must refuse to serve that org's
  data from a shared/default store — never silently fall back.
- **Config-driven, not code-driven, per-org source list.** Which repos/
  providers an org ingests from is org config (mirrors how
  `output/config/{org}_config.json` drives Gmail/Slack/Jira today), not an
  env var or a code change per org.
- **Provider-agnostic ingestion contract.** GitLab, GitHub, and future
  code hosts (Bitbucket, Gitea, self-hosted) must all normalize into the
  same shape before hitting shared processing — see
  `docs/source-plugin-contract.md` for a prior design pass at this exact
  problem (plugin interface + NormalizedRecord shapes); reuse that
  thinking rather than re-deriving it, even though it predates the ACL
  requirements above and will need the scope/ACL layer added on top.

## 4. Consistency with the rest of the platform

- **Same embedding model as everything else** the platform searches
  semantically (currently 2048-d NVIDIA NeMo, per `pipeline/embed_knowledge.py`
  and `builders/unified_builder.py`) — a different model on code embeddings
  means code and knowledge can never be searched together, which defeats
  half the point of a unified graph.
- **Postgres + pgvector is the locked storage decision** for chronograph's
  successor (already decided separately from this doc — do not re-propose
  Neo4j for code-intel storage; the ACL/scope requirements above apply
  regardless of which physical store holds the data).
- **Fits the existing pipeline shape**: fetch → normalize → (source-
  agnostic) process → validate → build artifacts, the same stage
  discipline every other source in this repo follows. The replacement is
  a new pipeline that happens to serve code hosts, not a bolted-on
  special case.

## 5. Explicitly out of scope for this requirements doc

- Exact schema, table names, file layout — that's the design doc.
- Which providers ship first (GitLab vs GitHub) — sequencing decision,
  not a requirement.
- Real-time webhooks vs batch re-crawl — follow whatever the rest of the
  ACL capture plan decides (currently batch-only, per
  `ACL_CAPTURE_PLAN.md` §8).
- The agentic query/chat layer on top of code-intel — separate concern,
  build after the data layer is trustworthy.

---

## 6. Definition of done

The replacement is ready to come off "deferred" when:

- [ ] Every commit/symbol/PR/issue node carries a `scope_id` (+
      `extra_scope_ids` where needed), fail-closed, mirroring
      `ACL_CAPTURE_PLAN.md`'s model.
- [ ] Repo membership/visibility is captured at ingestion and drives
      `scope_principals`, the same as Slack channel membership does today.
- [ ] DB connections are resolved per-org from the registry, fail-closed,
      no flat DSN.
- [ ] An org can have N repos across N providers; no request-time
      "first repo" guessing.
- [ ] Query-time reads apply an explicit allow-set check per request, not
      just per-org DB routing.
- [ ] Commit/author identity joins the shared person registry.
- [ ] Embeddings use the same model as the rest of the platform.
