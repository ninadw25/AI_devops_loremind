# ExpertEdge Zoho Ingestion — Handover

**Date:** 2026-08-14 · **Client:** ExpertEdge (Xpertedge, a CA firm) · **Org slug:** `expertedge`
**Status:** L0 fetch complete and verified. Downstream normalization/registry/edges
running now (free, automatic). Claim extraction (the paid step) **not started** —
awaiting a go/no-go decision.

Read this top to bottom before touching anything. It's written so a fresh
person (or a fresh agent with no memory of this work) can pick this up cold.

---

## 1. What this client is

- One mailbox: `lokesh.gupta@xpertedge.in` (Zoho Mail, `data_center=in`,
  `org_id=60081467338`).
- Vault entry `secret/tenants/expertedge/zoho` has `mail_single_mailbox=true`
  set (must be — see §4, org-wide discovery would otherwise hard-fail and no
  run could ever have succeeded). Verify this in the Vault UI if anything
  about mailbox discovery looks wrong; don't assume it's still there.
- `org_id` is kept in that Vault entry **for Cliq's admin discovery**, not
  for mail routing — `mail_single_mailbox` is what tells the mail fetcher to
  skip org-wide enumeration. These are deliberately separate flags
  (`infra/zoho/fetcher.py::fetch_mail_accounts` docstring explains why).
- No Cliq work has been done. No Gmail/Slack/Jira source exists for this
  tenant — Zoho Mail is the only connector wired up.

## 2. Infra map

| What | Where |
|---|---|
| Ingestion box | `ec2-15-206-15-12.ap-south-1.compute.amazonaws.com`, user `ubuntu`, key `loremind-gitlab-key` |
| Repo on box | `/opt/lore-ingestion` (venv inside: `/opt/lore-ingestion/.venv`) |
| systemd service | `lore-ingestion` (control API) — **restarting it kills any in-flight run**, the whole process group goes down |
| Control API | `localhost:8100`, header `X-Internal-Auth`, secret at `/srv/lore/lore.env` → `CONTROL_INTERNAL_SECRET` |
| Env file | `/srv/lore/lore.env` — has `VAULT_PROXY_URL`, `CONTROL_INTERNAL_SECRET`, `ZOHO_MAIL_SCANBACK_DAYS`. **Not sourced into your login shell automatically** — every manual on-box command needs `set -a && source /srv/lore/lore.env && set +a` first, or it silently falls back to wrong/missing config (bit us twice this session). |
| Postgres | RDS `loremind-lore-data-prod` (ap-south-1, `db.t4g.small`), database **`expertedge`** (each tenant is a separate database on this one shared instance — confirmed via `aws rds describe-db-instances --profile admin`). DSN is resolved from Vault at run/daemon launch, not stored in any `.env`. |
| Logs | `/srv/lore-state/logs/control_run_{run_id}.log` (control-API-triggered runs), `output/logs/*.log` on the box (manually-launched daemons, e.g. `substrate_flow_expertedge.log`) |
| AWS CLI | `admin` profile has RDS describe access; there's also a `client-expertedge` profile — untested this session, may be scoped narrower |

## 3. What's actually done — L0 fetch, verified complete

**131,205 messages**, spanning 2007-11-26 → 2026-08-14, across **all ~30
folders** in the mailbox — including Sent (3,205), Drafts (101), and Spam (22),
which a bug had silently skipped entirely until today. Completeness was
verified by exhaustion: every folder paged to an empty page, run exit code 0
across all of them. Zoho's API has no folder message-count field, so "paged to
empty in every folder" is the strongest completeness signal available — there
is no ground-truth total to check against.

Not fetched: **attachment content**. Only a `hasAttachment: true/false` flag
per message is captured — no PDF/DOCX/XLSX bytes. If ExpertEdge's real value is
in attached documents (plausible for a CA firm — tax filings, invoices), that's
unbuilt scope: Zoho's attachment-download API, file-type text extraction,
storage, and an ACL story for file content.

One folder (`[Gmail]/All Mail`, a leftover from an apparent old Gmail
migration) came back with zero messages. Plausible it's genuinely empty
(its content likely already lives in `Archive_Import/2025` and `/2026`), but
this was never independently confirmed — flagged, not fixed.

## 4. Bugs found and fixed this engagement (chronological, all on `main`)

All in `lore-ingestion`, branch `feat/zoho-backfill-throughput`, merged via
individual small MRs. In order:

1. `de60f45` / `5c5896a` — Zoho's list endpoint silently caps `limit` at 100;
   the fetcher asked for 200 and misread the always-100 response as "last
   page," so every mailbox stopped after its newest 100 messages.
2. `4603d63` — access tokens expire hourly; the fetcher grabbed one token per
   mailbox instead of per-request, so any backfill running past 60 minutes
   401'd mid-mailbox.
3. `d16a31e` / `01cdc20` — `GET /accounts` only returns the token grantor's
   OWN mailbox, not the org's. Added org-wide enumeration via
   `/organization/{org_id}/accounts`; made it **fail loudly** rather than
   silently fall back to one mailbox on a scope error.
4. `17feef3` / `55b455c` — `org_id` is needed by Cliq even for a genuinely
   single-mailbox tenant, so "clear org_id to fix mail" would have broken
   Cliq. Added a separate explicit `mail_single_mailbox` flag instead.
5. `223f09f` — the ops dashboard 500'd on any tenant (like this one, at the
   time) that hadn't reached a given pipeline layer yet, because it queried
   tables that don't exist until that layer's first run.
6. `579baca` — **the big one this session.** `/messages/view` was called with
   no `folderId`, and Zoho's docs never state what folders that covers by
   default. In production it covered 17 of 30 folders and skipped **Sent
   entirely** — zero outgoing correspondence ever replicated, reported as a
   clean successful run. Rewrote to enumerate every folder via
   `/accounts/{id}/folders` and page `/messages/view` once per folder with
   `folderId` explicit, deduping a message seen under more than one folder
   (Zoho has aggregate folders like "All Mail") within a single run so it's
   only ever bought once.
7. `6fe72af` — `runners/substrate_flow.py` (the downstream daemon) crashed on
   any brand-new tenant because it queried `lore.claim`, a table that doesn't
   exist until `extract_claims.py` creates it on its own first run. Extracted
   the existing observability fix into a shared `builders.l0_ledger.safe_scalar`
   helper so both callers share one fix.
8. `5884f81` — even after #7, the daemon still did nothing for expertedge: it
   seeds its "did anything change" baseline from the SAME counts it logs at
   startup, so a tenant fetched entirely *before* the daemon was ever started
   compared its counts to itself and concluded nothing needed processing.
   Fixed by seeding the baseline with a sentinel instead of the real counts,
   so the first cycle always processes whatever backlog already exists.

Every fix has a regression test (`infra/zoho/tests/test_fetcher.py`,
`pipeline/tests/test_observability_scalar.py`,
`pipeline/tests/test_substrate_flow_first_cycle.py`). Full suite: run
`../.venv/bin/python -m pytest pipeline/tests/ infra/ -q` from `lore-ingestion/`
— should show ~485 passing.

## 5. Pipeline status right now (00_DESIGN.md step numbers)

`docs/revised_ingestion_pipeline/00_DESIGN.md` is **cited throughout the
codebase by section number but does not exist in this repo or its git
history** — it must live outside version control. The step table below is
reconstructed from every `build step N` docstring citation across
`builders/*.py` and `runners/*.py`; treat it as inferred, not quoted.

| Step | What | Code | expertedge status |
|---|---|---|---|
| 1a fetch | `runners/zoho_ledger_sync.py` | ✅ Done — 131,205 msgs, all folders |
| 1b normalize | `builders/normalize_events.py` | 🔄 Running (daemon-driven) |
| 1c classify noise | `builders/classify_noise.py` | Chained automatically after 1b |
| 1d group conversations | `builders/group_conversations.py` | Chained automatically after 1b |
| 2 L1 entity registries | `builders/l1_registry.py` | Automatic once `events` changes |
| 3 L2 interaction graph | `builders/l2_edges.py` | Automatic once `events` changes |
| 4 claim extraction quality gate | `docs/revised_ingestion_pipeline/CLAIM_GATE_RESULT.md` | ✅ Passed (method-level, not expertedge-specific — Kimi K2.5 via Bedrock, 100% grounded evidence on a 40-conversation sample) |
| 5 claim dedup | `builders/dedup_claims.py` | Built; nothing to dedup until step 7 runs |
| 6 | — not cited anywhere in code | unknown — possibly the edit/delete reconciliation loop in the separate, Gmail/Slack/Jira-scoped `docs/sync_engine/SYNC_ENGINE_DESIGN.md`, which explicitly does not cover Zoho |
| 7 claim extraction + Neo4j projection | `runners/extract_claims.py` + `builders/project_claims.py` | ❌ **Not started — the paid step, needs a decision** |

Steps 1b–3 are free (no LLM calls) and run automatically via the
`runners/substrate_flow.py` daemon, which is running right now on the box
(`--org expertedge --interval 300`, logging to
`output/logs/substrate_flow_expertedge.log`). **Do not restart it** without
checking whether it's mid-cycle — same rule as the fetch: interrupting costs
re-work, though much less than the original fetch since normalize/L1/L2 are
cheap and idempotent.

## 6. The one real decision pending: start claim extraction?

`runners/extract_claims.py`, model `moonshotai.kimi-k2.5` via Bedrock,
pricing `$0.6/M input + $3.0/M output tokens`. Cost isn't estimable precisely
yet — it bills per conversation, and the conversation count only becomes known
once step 1d (grouping) finishes. Launch command (adapt from
`scripts/extract_claims_overnight.sh`, which is hardcoded to `ezobooks` — do
not run that script as-is against expertedge):

```bash
cd /opt/lore-ingestion
set -a && source /srv/lore/lore.env && set +a
setsid nohup .venv/bin/python -u -m runners.extract_claims --org expertedge \
    --concurrency 12 --max-concurrency 48 --batch 400 --min-messages 2 \
    >> output/logs/extract_claims_expertedge.log 2>&1 &
disown
```

`--min-messages 2` skips single-message "conversations" (mostly
notifications/newsletters) — matches the precedent set for ezobooks. Live
progress: `../.venv/bin/python scripts/claims_progress.py` (reads the DB, not
the log, so it can't go stale).

Once extraction produces claims, `substrate_flow` picks them up automatically
(dedup + claims-only Neo4j projection) — no separate manual step.

## 7. Outstanding / not done

- **Secret rotation** — `CONTROL_INTERNAL_SECRET`, the `expertedge` Postgres
  role password, and the Zoho refresh token were all pasted in plaintext into
  terminal scrollback (and this chat) at some point during onboarding. Not
  rotated. Do this before treating the environment as clean.
- **Attachments** — not fetched at all, see §3.
- **`[Gmail]/All Mail`** — unverified whether genuinely empty or a gap, see §3.
- **Incremental sync isn't folder-scoped-tested** — the fix in `579baca`
  changed `fetch_mail_for_account`, which both the full backfill AND the
  regular incremental watermark sync call. It should Just Work for future
  incremental syncs since it's the same function, but that path hasn't been
  exercised live yet for this tenant (only the full backfill has run).
- **Zoho has no edit/delete reconciliation.** `SYNC_ENGINE_DESIGN.md`'s
  three-loop model (delta/backfill/reconcile) explicitly scopes itself to
  Gmail/Slack/Jira only. Zoho uses the simpler one-shot watermark cursor in
  `ZOHO_ON_SUBSTRATE.md` instead — if a message gets edited or deleted in
  Zoho after we've fetched it, nothing here will notice.
- **Resume cost on interruption** — Zoho's list endpoint is offset-based with
  no date filter, so resuming a partial fetch means re-listing from `start=1`
  in every folder to find where it left off (bodies aren't re-bought, thanks
  to `skip_ids`, but the list-call cost is real). Not fixed, just known.
- **`docs/revised_ingestion_pipeline/00_DESIGN.md`** doesn't exist in this
  repo despite being cited everywhere (§5). Worth tracking down and
  committing it, or updating every citing docstring if it's truly gone.

## 8. Quick status-check commands

```bash
# Is the flow daemon alive and what's it doing
ps aux | grep "[s]ubstrate_flow"
tail -30 /opt/lore-ingestion/output/logs/substrate_flow_expertedge.log

# Control API run history (needs CONTROL_INTERNAL_SECRET from /srv/lore/lore.env)
curl -s -H "X-Internal-Auth: $CONTROL_INTERNAL_SECRET" "localhost:8100/runs?org=expertedge" | jq .
curl -s -H "X-Internal-Auth: $CONTROL_INTERNAL_SECRET" "localhost:8100/daemons" | jq .
```

```sql
-- DataGrip, against the `expertedge` database on loremind-lore-data-prod

-- pipeline progress across layers
SELECT
  (SELECT count(*) FROM lore.source_payloads WHERE org='expertedge') AS payloads,
  (SELECT count(*) FROM lore.events WHERE org='expertedge') AS events,
  (SELECT count(*) FROM lore.entity_person WHERE org='expertedge') AS persons,
  (SELECT to_regclass('lore.claim')) AS claim_table_exists;
```

---

*Written by Claude during a live debugging session, 2026-08-14. Every claim
above traces to a command actually run and pasted back during that session —
ping the previous conversation owner if anything here looks stale.*
