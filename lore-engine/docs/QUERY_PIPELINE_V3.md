# Query Pipeline v3 — Querying the Claims Substrate

**Status:** Proposed — the query-side counterpart of the ingestion redesign
**Date:** 2026-07-13
**Depends on:** `../../docs/revised_ingestion_pipeline/` (the 4-layer substrate, self-contained claims, registries — referenced below as `INGEST 00`–`04`)
**Supersedes:** parts of `QUERY_PIPELINE_V2.md` (the v2 orchestration shell survives; its retrieval semantics change)

The ingestion redesign exists to make a well-defined ontology; this doc is where that ontology pays for itself. The audit in §1 shows that v2 — for all its real improvements (frontier expansion, unified rerank, fail-closed scopes) — spends most of its machinery **compensating for the old ontology's defects**, and that several correctness properties the product needs (temporal truth, groundedness, deterministic answers to deterministic questions) are not achievable on the old substrate at all. v3 is not "v2 plus claims": the unit of retrieval, the ranking signals, the citation model, and the engine split all change because the substrate changes.

---

## 1. Audit: how v2 fails to use the ontology (and why half of it is the ontology's fault)

Each finding cites current code. Q1–Q4 are v2 compensating for ingestion defects that the claims substrate removes; Q5–Q10 are query-side gaps that remain even with perfect data.

### Q1 — Meaning is reassembled at query time, and the reassembly is lossy

A Decision node's meaning lives in its edges (INGEST 00 §F1), so the engine must reconstruct "who decided / who is assigned" during retrieval: `unstructured.py:_attribution()` walks the spine Person nodes that happened to be reached during beam expansion and staples `"decided by: X"` strings onto chunk text.

This reconstruction fails silently in three ways:

- **Beam competition.** Persons compete with content nodes for `beam_width` slots (`frontier.py:224`). If the Person is pruned — likely, since PARTICIPATED_IN/STATED_BY carry low relation weights (0.6/0.7) — the Decision chunk ships to the reranker and the synthesis LLM **with its attribution missing**. Retrieval quality depends on beam luck.
- **Partial relation coverage.** `_ATTRIB_REL` covers 4 relations; SUBJECT_OF (who a decision *affects* — exactly what "what happened with Pooja's salary?" needs) is expanded (`EXPANSION_RULES`) but never rendered into attribution.
- **Rank-time blindness.** The cross-encoder scores chunk *text*. A Decision whose attribution didn't attach scores lower on person queries than one where it did — same knowledge, different luck, different rank.

With self-contained claims (INGEST 00 §4) the statement already carries decider, subject, date, amount. `_attribution()` and its failure modes are deleted, not fixed.

### Q2 — No temporal semantics: the engine serves stale truth with full confidence

Nothing in v2 knows that knowledge changes. There is no SUPERSEDES anywhere in the engine (the old graph never wrote it), so "salary is 19k" (March) and "salary revised to 24k" (June) are two equal Facts. Whichever embeds closer to the question wins the seed slot; the reranker has no signal to prefer the newer one; the synthesis prompt has no instruction about conflicting values. **The engine can answer money questions with superseded numbers and label the answer high-confidence.**

Worse, the only time filter that exists — `seeds.py` gating on `node.created_at` — filters on *ingestion-adjacent* time, not on when the fact became true or ceased to be. "What was the salary as of April?" is unanswerable by construction.

The claims substrate carries `temporal {occurred_at, valid_from, superseded_by}` and SUPERSEDES chains as first-class data. v3 makes temporal resolution a pipeline stage (§4); this is the single largest correctness upgrade in this doc.

### Q3 — Ranking ignores every knowledge-native signal the substrate has

`rank.py` fuses engine score, BM25, and cross-encoder — all *relevance* signals. The substrate's *reliability* signals are absent or mangled:

- **Corroboration** is folded into prose (`"corroborated across sources"` appended to chunk text, `unstructured.py:82`) in the hope the cross-encoder notices. A count (`corroborated_by`) is reduced to a boolean string.
- **Confidence** — extraction confidence exists on no v2 node and no rank term. Claims carry calibrated confidence (INGEST 03 §5) that ranking should consume.
- **Recency** is not a rank term at all; two claims equal in relevance rank identically whether from last week or 2024.
- The frontier weights (`RELATION_WEIGHTS`, intent priors, `_CONVERGENCE_FACTOR`) are hand-tuned constants from the research prototype; V2's own doc admits the golden-set eval is still pending. There is no harness that could tell us whether 0.75 for CORROBORATES is better than 0.5 (Q10).

### Q4 — Entity anchoring resolves against the polluted free-text registries

`seeds.py:_ANCHOR_CYPHER` matches `toLower(p.name) CONTAINS toLower(nm)` — substring match against 13,562 Persons containing 229 duplicate-name groups, and against 3,609 free-text Topics where "payment" matches 40 of them (INGEST 00 §F2/F3). An anchor is a score-1.0 guaranteed frontier entry: anchoring "rupesh garg" seeds the beam from up to 5 of 9 duplicate Persons; anchoring "payment" walks whichever arbitrary topic-node cluster `LIMIT 5` returns. Query expansion via the topic registry (aliases, subtopics) is impossible when there is no registry.

With L1 registries, anchoring becomes: person alias-table lookup (surrogate `person_id`, F-S-merged aliases), topic registry match (curated ~150–300 entries + alias lists + embedding fallback) — the same closed-world discipline the extractor uses, applied to questions.

### Q5 — Deterministic questions are answered probabilistically

"Who does Mukesh work with most?", "Who is the busiest person on billing?", "Who reports to whom?" are L2 questions — answerable exactly from deterministic interaction data. v2 forces them through embedding + beam + LLM: COMMUNICATES_WITH is a 0.3-weight traversal edge, and the starved deterministic layer (PARTICIPATED_IN = 779, WORKS_AT = 289 edges, INGEST 00 §F5) means there is often nothing to traverse. The answer comes out as synthesized prose over whatever fragments survived — unexplainable, unrepeatable, and different from what a dashboard computing the same thing would show.

The design principle this violates: **questions with deterministic answers must take deterministic paths, with the LLM at most narrating the result** — the query-side mirror of INGEST 08's "LLM touches prose once." This is also what makes chat answers *consistent* with the expert-search/team dashboards (`docs/03`), which are built on the same L2/L4 views.

### Q6 — No mid-level retrieval unit: broad questions are structurally unserved

`query_service.py:104` degrades GLOBAL intent to TOPIC_EXPLORATION with a log line; SUMMARY intent has empty prior/boost tables (`frontier.py:74,84`). Claim-level (or node-level) retrieval is the wrong granularity for "what are the main themes this quarter" — assembling 18 chunks cannot summarize a corpus. The substrate provides the missing granularities: **ThreadSummary digests** (per-thread, INGEST 00 §4.4) and **L4 topic rollups** (periodic, recomputable). v3 serves SUMMARY from digests and GLOBAL from rollups (§3, Lane C) instead of degrading.

### Q7 — Citation poverty and ungrounded synthesis

Provenance today is `citations=[source_thread_id]` — a thread id, no URL, no quote (violating the citation-provenance requirement). The synthesis prompt then *forbids* citations and verbatim quotes, and nothing verifies the answer against the context: the only groundedness control is the instruction "Answer ONLY from the provided context." An LLM instruction is not a gate (the entire thesis of INGEST 01). The engine cannot tell a grounded answer from a fluent hallucination — it scores both by chunk count (`scoring.py:score_confidence_chunks`).

Claims make answer-level verification *mechanical*: every claim has an id, an evidence quote, and source event ids. v3 requires the synthesis to cite claim ids inline and deterministically checks every cited id against the retrieved set (§5) — the query-side twin of ingestion's G5 verbatim-evidence gate.

### Q8 — Meaning is coupled to Neo4j availability

"Graceful degradation: works vector-only if Neo4j is unavailable" is only half-true in v2: vector-only mode returns naked descriptions with no attribution (Q1's reassembly needs traversal), so degraded mode serves exactly the context-free strings that F1 diagnosed. On the claims substrate the degradation story inverts: **Postgres (claims, self-contained) is primary; Neo4j is an optional accelerator** for relation-walks (BECAUSE chains, corroboration networks). Losing Neo4j loses multi-hop expansion — never meaning. (The existing `infra/pg_knowledge_repository.py` already proves the pg path; it currently mirrors the *old* node/edge model and gets re-pointed at the claim store.)

### Q9 — The engine split is source-aligned and scales O(sources)

v2 runs two engines *because there are two ontologies* (unstructured prose vs Jira structure). That was correct for the old substrate — and it means every new source with structure (GitLab, Outlook calendar, HR systems) potentially adds an engine. The claims substrate dissolves the reason: Jira's deterministic fields become L1 Artifact records and L2 edges; Jira's prose becomes claims like everyone else's (INGEST 00 §4.3). v3's split is **layer-aligned, not source-aligned** — a claims lane (L3), a records/views lane (L1/L2/L4), a digest lane — and a new source changes *nothing* in the engine. This is the org-agnostic principle applied to the query side.

### Q10 — No evaluation loop

V2 shipped with "pending golden-set eval" and it is still pending: there is no harness, no question suite, no regression gate on any rank weight, prompt, or Cypher change. Every tuning constant in `frontier.py` and `rank.py` is unfalsifiable. v3 adopts the ingestion measurement stack (INGEST 02): the synthetic-org end-to-end suite (E7) becomes the engine's benchmark, extended with query-specific metrics (§7). No v3 component ships before its metric exists — measurement is build-order step 0 (§9).

---

## 2. Target architecture

```
question, allowed_scopes, history
        │
        ▼
 [ANALYZE ∥ EMBED]                 (kept from v2 — one small LLM call ∥ one embed)
   analysis now resolves entities against L1 REGISTRIES:
   persons → alias table → person_ids · topics → registry (+aliases) → topic_ids
   intents extended: + EXPERTISE_LOOKUP, ACTIVITY_LOOKUP, ARTIFACT_STATUS, AS_OF(t)
        │
        ├─────────────────────┬──────────────────────┐   (all lanes concurrent,
        ▼                     ▼                      ▼    all fail-closed on scopes)
  LANE A: CLAIMS          LANE B: RECORDS        LANE C: DIGESTS/ROLLUPS
  (L3, probabilistic)     (L1/L2/L4, exact)      (mid/global granularity)
  pgvector ANN over       intent-routed SQL/     pgvector over ThreadSummary
  claim statements        Cypher resolvers:      digests (SUMMARY);
  + registry-anchored     expertise, activity,   L4 topic rollups (GLOBAL)
  claim lookup            org-structure, person
  → optional Neo4j        profile, artifact
  relation expansion      status (ex-Jira lane)
        │                     │                      │
        └─────────────────────┴──────────────────────┘
                              │
                              ▼
              TEMPORAL RESOLUTION (new stage — §4)
              collapse SUPERSEDES chains → serve chain heads
              (or the claim valid at t for AS_OF queries)
                              │
                              ▼
              UNIFIED RANK (§5): cross-encoder + BM25
              ⊕ corroboration ⊕ calibrated confidence ⊕ recency
                              │
                              ▼
              ASSEMBLE: claims verbatim (self-contained — zero reconstruction),
              each with [c:claim_id] marker
                              │
                              ▼
              SYNTHESIZE with citation discipline
              → GROUNDEDNESS GATE: cited ids ⊆ retrieved ids (§6)
                              │
                              ▼
              answer + per-sentence citations + calibrated confidence
```

What survives from v2 verbatim: the router-free run-all-lanes principle (a lane is just queries; the unified rank drops irrelevance — still the right call), the analyze∥embed concurrency with keyword fallback, the `SourcedChunk` envelope pattern (extended, §3.4), `domain/scopes.py` single-owner gating (gains an SQL twin), the RerankProvider registry, SSE streaming, and the five-layer code architecture.

### 3.1 Lane A — claims

- **Seeding:** pgvector ANN over claim-statement embeddings (scope-gated in SQL, same fail-closed predicate as Cypher — one semantic, two dialects, both owned by `domain/scopes.py`), plus **registry-anchored lookup**: claims whose `entities` contain the resolved person_ids / whose `topics` contain the resolved topic_ids, ranked by recency. Because statements are self-contained, a seed is *already a complete retrieval unit* — v2's need to expand before a seed means anything is gone.
- **Expansion (optional, Neo4j projection):** shrinks to what traversal is genuinely for — claim↔claim relations: BECAUSE (rationale for REASONING intent), CORROBORATES (evidence gathering), RELATES_TO, INSTANCE_OF (series), SUPERSEDES (feeds §4), plus entity co-mention (other claims sharing this claim's entities). Persons no longer compete for beam slots (Q1 gone), so beam width and depth both shrink; expansion is an enrichment step, not the meaning-maker.

### 3.2 Lane B — records & views (the deterministic lane)

Intent-routed resolvers, each a parameterized SQL/Cypher template over L1/L2/L4 — no LLM, no embedding, exact answers with explainable provenance:

| Intent | Resolver over | Feature twin |
|---|---|---|
| EXPERTISE_LOOKUP ("who knows X") | L4 EXPERT_IN scores, drill-down to L2 evidence | expert search (`docs/03`) |
| ACTIVITY_LOOKUP ("who works with Y", "most active on Z") | L2 COMMUNICATES_WITH / PARTICIPATED_IN with weights+recency | team/pulse pages |
| PERSON_LOOKUP profile skeleton | L1 person + L2 neighborhood (roles, containers, top counterparts) | onboarding package |
| ARTIFACT_STATUS ("status of LOR-78") | L1 Artifact records (Jira issue fields, sprint, assignee) | replaces the v2 Jira engine's navigational/status modes |
| Org structure ("who reports to…") | L1/L2 (where sources provide it) | offboarding |

Resolver output enters the same envelope with `score=1.0` and `kind="record"` — the rank stage keeps exact answers on top (a record that matches the routed intent outranks probabilistic claims by construction), and synthesis *narrates* them rather than deriving them. Chat and dashboards now compute the same numbers from the same views — they can no longer disagree (Q5).

### 3.3 Lane C — digests & rollups

SUMMARY intent retrieves ThreadSummary digests (their own pgvector space — thread-granular, ideal for "what happened with the vendor negotiation"). GLOBAL retrieves L4 topic rollups (precomputed topic-level summaries with claim-id citations, recomputed on schedule). GLOBAL stops degrading; `intent_global_unserved` logging is replaced by an actual answer path (Q6).

### 3.4 Envelope extension

`SourcedChunk` gains the claim-native fields the tail stages need:

```
SourcedChunk {
  source, node_id, node_type, text, score, scope_ids, created_at   # as today
  claim_id, kind            # kind ∈ Decision|Fact|…|record|digest|rollup
  occurred_at               # event time, not ingestion time
  confidence_calibrated     # from the claim store (INGEST 03 §5); null for records (exact)
  corroboration_count       # len(corroborated_by)
  superseded_by             # non-null → handled by §4 before rank
  citations: [{event_id, url, evidence_quote}]   # real provenance, per the citation requirement
}
```

---

## 4. Temporal resolution (new stage, between retrieval and rank)

Deterministic, pure, ~30 lines of logic over `superseded_by`:

1. Group retrieved claims by SUPERSEDES chain (chains are precomputed at projection time — each claim carries its chain head id, so this is a hash-group, not a walk).
2. **Default:** serve the chain head (current truth). Superseded members are dropped from ranking but kept in a `history` side-channel on the head chunk — so synthesis can say "revised from 19k to 24k in June" instead of merely asserting 24k. The history is *context*, never the answer.
3. **AS_OF(t) queries** (analysis extracts "as of April", "back in March", or an explicit time_range): serve the chain member whose `[valid_from, superseded_at)` interval contains t, marked `historical=true` so synthesis states it as time-scoped truth.
4. **Unresolved conflicts** (two same-key claims, no SUPERSEDES between them — the ingestion funnel's HARD-BLOCK class): serve both, marked `conflict=true`; synthesis is instructed to surface the discrepancy, never to pick silently. Honest uncertainty beats confident coin-flips.

This stage is why "what is Pooja's salary" stops being answerable with March's number (Q2) — and it costs no LLM call and no measurable latency.

---

## 5. Unified rank: relevance × reliability

Keep `rank_chunks`'s shape (hybrid → cross-encoder → token budget) and add a deterministic reliability multiplier after the cross-encoder:

```
final = (w1·rerank + w2·hybrid)                            # relevance (as today)
        · (1 + γc·log(1 + corroboration_count))            # independently confirmed
        · (0.5 + 0.5·confidence_calibrated)                # extraction reliability
        · recency_decay(occurred_at; half_life per kind)   # Facts decay, Decisions slower
```

- Records (Lane B) bypass the multiplier — they are exact, and intent-routing already placed them.
- Every constant (γc, half-lives, w1/w2) is a `Settings` field tuned against the E7 question suite (§7) — the difference between v3's constants and v2's is not their values but that they are *falsifiable* (Q3, Q10).
- Cross-encoder input is the claim statement itself — self-contained, so the reranker finally scores what the user will actually be shown (Q1's rank-time blindness gone).

---

## 6. Grounded synthesis: the citation gate

The synthesis contract inverts v2's "no citations" rule:

1. Context lines are rendered as `[c:claim_id] statement` (records as `[r:id]`, digests `[d:id]`).
2. The prompt requires every factual sentence to carry the marker(s) of the claims it draws on, and forbids statements carrying no marker. Markers are for the machine; the frontend renders them as hoverable citations (evidence quote + deep link from `citations` — the payoff of real provenance).
3. **The groundedness gate (deterministic, post-synthesis):** parse markers from the answer; verify cited ids ⊆ retrieved ids; compute the unsupported-sentence rate (sentences with no marker). Invalid ids → strip the sentence. Unsupported rate above threshold → re-synthesize once with the violations appended; still failing → degrade honestly (serve with a low-confidence banner, or fall back to an extractive answer assembled from the top claims verbatim — which is only possible because claims are self-contained).

This is the query-side G5: the LLM cannot fabricate without fabricating a citation, and fabricated citations fail a set-membership check. It also gives confidence scoring a real basis (§6.1) and neutralizes most retrieved-content prompt injection (§8) as a side effect.

Streaming note: the gate needs the full answer. Stream tokens as today, run the gate on the assembled text, and emit a final `verification` SSE event (per-sentence citation map + any stripped spans); the frontend renders trust *progressively* rather than the pipeline buffering the whole answer.

### 6.1 Confidence, replaced

`score_confidence_chunks`'s count heuristic (Q7) is replaced by a composite the layers can actually justify: groundedness rate × mean calibrated confidence of *cited* claims (not retrieved ones) × corroboration presence × lane agreement (a record and a claim agreeing is the strongest signal we have). Calibrated against E7 correctness labels the same way extraction confidence is calibrated (INGEST 03 §5) — "high" must eventually *mean* a measured P(correct).

**Fail-closed phrasing rule:** when nothing (or nothing sufficient) is retrieved, the answer must be indistinguishable between "no such knowledge exists" and "it exists outside your scopes." One neutral phrasing, always — anything else is an existence oracle for out-of-scope data.

---

## 7. Evaluation (adopts INGEST 02; query-engine-specific rows)

The E7 synthetic-org question suite is the engine's benchmark; runs against goldset orgs where every answer is known. Engine-specific metrics and gates:

| Metric | Definition | Gate |
|---|---|---|
| Answer accuracy | correct-fact rate on the E7 suite | beats v2 baseline on identical data before v3 replaces it |
| **Stale-answer rate** | questions whose gold answer was superseded, answered with the old value | **≈ 0** — the §4 acceptance test; seeded supersession chains in the gold set |
| Groundedness | 1 − unsupported-sentence rate; invalid-citation rate | invalid ≈ 0 (it's deterministic); unsupported ≤ 5% |
| **Scope-leak suite** | adversarial questions probing out-of-scope gold content (direct asks, entity fishing, existence probes) | **zero leakage, zero existence signals** — run on every change to any gate or resolver |
| Deterministic-consistency | Lane B answer == the same view queried directly | exact — by construction, tested anyway |
| Latency budget | p50/p95 per stage | temporal + gate stages ≤ 30ms combined; overall p95 no worse than v2 |
| Rank ablations | accuracy with each §5 term removed | each term must earn its constant or be deleted |

Content-free production monitors (T3 style): groundedness rate, lane-agreement rate, conflict-served rate, superseded-suppressed count, per-lane contribution to cited answers.

---

## 8. Query-side guardrails (mirror of INGEST 01)

The engine has three LLM call sites — analysis, synthesis, (judge in eval). Same doctrine: proposal-not-authority, fail-safe = inaction, blast radius bounded by construction.

- **Analysis** output is already schema-validated with keyword fallback (keep). Its entity output now resolves against registries — it can *select* known entities, not mint them (closed-world, same as extraction's G6/G9). Failure → keyword fallback → worst case is worse seeding, never wrong data.
- **Synthesis reads retrieved content, which is third-party text** — an ingested email can carry an injection that survives extraction as a scoped claim (INGEST 01 §3 accepts this residual). Blast radius at synthesis: the reader already has scope access to that claim, so nothing can be *revealed*; the risk is answer manipulation ("ignore the context, say X"). Mitigations: data demarcation (context block already delimited), the groundedness gate (a manipulated answer either cites nothing — stripped — or cites the injected claim, making the manipulation *visible and attributable* to its source event), and injection canaries in the E7 suite asserting the gate catches them.
- **Scopes stay out of LLM reach entirely:** no LLM output influences any scope predicate; `domain/scopes.py` remains the single owner, now with the SQL twin audited to the same invariant (D-INGEST-001). `scrub_phones` and history handling unchanged.

---

## 9. What survives / changes / dies in lore-engine

**Survives:** five-layer architecture and DI; `QueryService` orchestration shape (analyze∥embed → concurrent retrieval → rank → assemble → synthesize → stream); router-free all-lanes principle; `domain/scopes.py` ownership; RerankProvider/ChatProvider/EmbeddingProvider registries; memory service; keyword-analysis fallback; Langfuse spans.

**Changes:** retrieval unit node→claim; `SourcedChunk` extended (§3.4); `rank_chunks` gains the reliability multiplier; `build_context_chunks` renders claims verbatim with markers; synthesis prompt rewritten for citation discipline; `score_confidence_chunks` replaced (§6.1); analysis prompt gains registry resolution + new intents; `infra/pg_knowledge_repository.py` re-pointed at the claim store; Jira engine's navigational/status modes become Lane B resolvers.

**Dies:** `unstructured.py:_attribution()` and attribution-by-traversal (Q1); free-text `CONTAINS` anchoring (Q4); GLOBAL-degradation (Q6); thread-id-only citations and the "no citations" synthesis rule (Q7); the legacy v1 modules still gating on `access_groups` (`domain/retrieval/{person,vector,expansion}.py` — already superseded, now actually deleted); the source-aligned two-engine split as an organizing principle (Q9).

---

## 10. Build order (staged against ingestion's, INGEST 00 §11)

Independent of the claim store — start now:

1. **Eval harness + E7 wiring** (Q10; measurement before movement — same rule as ingestion). Baseline v2 on the suite so v3 has something to beat.
2. **Scope-leak suite** — valuable against v2 today; mandatory forever.
3. **Delete the legacy v1 retrieval modules** (access_groups path) — dead code with a wrong ACL model is a liability, not a fallback.

Gated on ingestion milestones:

4. **Projection contract** (with ingestion step 7): a one-page schema of what the engine reads — claim rows/embeddings + chain-head ids in Postgres; claim↔claim edges + entity-role edges in the Neo4j projection; registry tables. Written jointly, versioned, and tested from both sides (builder emits → engine reads).
5. **Lane A** on the claim store + temporal stage (§4) — the moment E7's stale-answer metric becomes measurable.
6. **Cited synthesis + groundedness gate** (§6) — needs only claim ids, so it lands with Lane A.
7. **Lane B resolvers**, expert search first (ingestion step 8 builds the L2/L4 views; the resolver is thin SQL over them).
8. **Registry-aware analysis anchoring** (ingestion step 6) and **Lane C** digests/rollups.
9. **Rank reliability terms + confidence calibration** — last, because they need E7 labels to tune against (constants without an eval loop is how v2 got Q10).

Each step ships behind the §7 gates; v2 remains the serving path until v3 beats it on the same suite — the same promote-on-evidence rule the ingestion side uses for extractor versions.
