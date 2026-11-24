> **Historical (as of 2026-09-25)** — superseded by the completed `docs/modularity-phases/` log and the current layout in `CLAUDE.md`. Kept for context; do not treat as current.

/modularity — Structural Audit: lore-ingestion/

  1. Current State Summary

  lore-ingestion is a 6-stage Gmail/GitLab ingestion pipeline (~15.5k LoC across
   common/, pipeline/, builders/, connectors/, chronograph/, evaluation/,
  knowledge_distillation/). The dependency graph is acyclic and mostly
  inward-flowing — common/ is the leaf, everything else depends on it. The real
  problems are not structural cycles. They are: (1) schema/ownership leakage
  (node-ID generation duplicated between validator and post-processor; two
  problems are not structural cycles. They are: (1) schema/ownership leakage
  (node-ID generation duplicated between validator and post-processor; two
  different embedding models coexist in the same pipeline; Cypher escaping
  copy-pasted), (2) scattered configuration (every module calls os.getenv()
  directly — there is no single config loader, unlike chronograph/config.py whic  (node-ID generation duplicated between validator and post-processor; two
  different embedding models coexist in the same pipeline; Cypher escaping
  copy-pasted), (2) scattered configuration (every module calls os.getenv()
  directly — there is no single config loader, unlike chronograph/config.py
  which is the lone exception), (3) god modules (pipeline/triple_extractor.py
  1273 LOC, pipeline/validator.py 755 LOC, builders/unified_builder.py 629 LOC,
  pipeline/filter.py 686 LOC), (4) doc/code drift in CLAUDE.md referencing files
   that no longer exist, and (5) parallel/redundant subsystems —
  knowledge_distillation/models/ is 10 near-identical 35-line files,
  chronograph/ has its own self-contained config while the Gmail half has none.
  The bones are good; the connective tissue is undisciplined.

  2. Dependency Graph (internal edges only)

         ┌──────────────────────────────────────────┐
         │   hashing.py  rbac.py  org_config.py     │
         │   output_paths.py  logger.py             │
         └──────────────────────────────────────────┘
                ▲           ▲          ▲       ▲
                │           │          │       │
         ┌──────┴────┐ ┌────┴────┐ ┌───┴────┐ ┌┴─────────┐
         │connectors/│ │pipeline/│ │builders/│ │evaluation/│
         │  gmail/   │ │  6 stgs │ │ 3 mods  │ │ 5 rounds │
         └───────────┘ └─────────┘ └─────────┘ └──────────┘
                            ▲
                            │  (knowledge_distillation imports pipeline.triple_extractor
                            │   and pipeline.validator — Layer-3 → Layer-2 OK direction)
                    ┌───────┴────────────────┐
                    │ knowledge_distillation/│
  the same pipeline; Cypher escaping
  copy-pasted), (2) scattered
  configuration (every module calls
  os.getenv() directly — there is no
  single config loader, unlike
  chronograph/config.py which is the lone
  exception), (3) god modules
  (pipeline/triple_extractor.py 1273 LOC,
  pipeline/validator.py 755 LOC,
  builders/unified_builder.py 629 LOC,
  pipeline/filter.py 686 LOC), (4)
  doc/code drift in CLAUDE.md referencing
  files that no longer exist, and (5)
  parallel/redundant subsystems —
  knowledge_distillation/models/ is 10
  near-identical 35-line files,
  chronograph/ has its own self-contained
  config while the Gmail half has none.
  The bones are good; the connective
  tissue is undisciplined.

  2. Dependency Graph (internal edges
  only)

         ┌────────────────────────────────
  ──────────┐
         │ common/  (Layer 0 — no internal
   imports) │
         │   schemas.py
  schemas_extraction.py      │
         │   hashing.py  rbac.py
  org_config.py     │
         │   output_paths.py  logger.py
            │
         └────────────────────────────────
  ──────────┘
                ▲           ▲          ▲
       ▲
                │           │          │
  (pipeline/triple_extractor.py 1273 LOC, pipeline/validator.py 755 LOC,
  builders/unified_builder.py 629 LOC, pipeline/filter.py 686 LOC), (4) doc/code drift
  in CLAUDE.md referencing files that no longer exist, and (5) parallel/redundant
  subsystems — knowledge_distillation/models/ is 10 near-identical 35-line files,
  chronograph/ has its own self-contained config while the Gmail half has none. The
  bones are good; the connective tissue is undisciplined.

  2. Dependency Graph (internal edges only)

         ┌──────────────────────────────────────────┐
         │ common/  (Layer 0 — no internal imports) │
         │   schemas.py  schemas_extraction.py      │
         │   hashing.py  rbac.py  org_config.py     │
         │   output_paths.py  logger.py             │
         └──────────────────────────────────────────┘
                ▲           ▲          ▲       ▲
                │           │          │       │
         ┌──────┴────┐ ┌────┴────┐ ┌───┴────┐ ┌┴─────────┐
         │connectors/│ │pipeline/│ │builders/│ │evaluation/│
         │  gmail/   │ │  6 stgs │ │ 3 mods  │ │ 5 rounds │
         └───────────┘ └─────────┘ └─────────┘ └──────────┘
                            ▲
                            │  (knowledge_distillation imports pipeline.triple_extractor
                            │   and pipeline.validator — Layer-3 → Layer-2 OK direction)
                    ┌───────┴────────────────┐
                    │ knowledge_distillation/│
                    └────────────────────────┘

         ┌──────────────────────────────────────┐
         │ chronograph/  (isolated sub-pipeline)│
         │   no edges to common/ — has its own  │
         │   config.py, entities.py             │
         └──────────────────────────────────────┘

  No cycles. No upward imports. The only "weird" edge is knowledge_distillation →
  pipeline.triple_extractor — but knowledge_distillation is orchestration over the
  production extractor, so the direction is correct.

  Notable: chronograph/ is its own island. It does not use common/logger,
  common/schemas, common/hashing, or common/org_config. It re-implements config loading
  and entity schemas internally.

  3. Question 1 — Who Owns the Truth?

  #: O1
  Conflict: Node-ID formula
  Files: pipeline/validator.py:697-709 and builders/post_processor.py:281, 340, 357, 368
  What both claim to own: Both compute f"{type}:{short_hash(...)}" IDs from raw strings
    using short_hash. Formula is duplicated, not delegated to common/hashing.py.
  Impact: If anyone changes the ID recipe in one place (e.g., adds normalization), the
    graph and vector index silently desync.
  ────────────────────────────────────────
  #: O2
  Conflict: Cypher escape function _esc
  Files: builders/unified_builder.py:37-40 and builders/post_processor.py:38-41
  What both claim to own: Both files define their own _esc() for Cypher string escaping.

    Subtly different — unified_builder also replaces ; with ,; post_processor does not.
  Impact: Cypher generated by post_processor can contain ; and break batch loading. Real

    data-corruption risk.
  ────────────────────────────────────────
  #: O3
  Conflict: Embedding model
  Files: pipeline/embed_knowledge.py:40 (NVIDIA llama-3.2-nemoretriever-300m),
    builders/vector/build_index.py:42 (OpenAI text-embedding-3-small),
    builders/vector/build_index.py:36 (local all-MiniLM-L6-v2 fallback)
  What both claim to own: Three different embedding models can produce the "vector
  index"
    depending on which path you ran. Dimensions differ across them (1024 vs 1536 vs
  384).
  Impact: If the Neo4j vector index was uploaded by unified_builder (NVIDIA, 1024-dim)
    but the backend's standalone vector index was built by build_index.py (OpenAI,
    1536-dim), they answer different questions. Today, which one is canonical depends on

    which command the operator ran.
  ────────────────────────────────────────
  #: O4
  Conflict: LLM provider config
  Files: pipeline/triple_extractor.py:1094-1097, builders/post_processor.py:148-150,
    pipeline/embed_knowledge.py:39, 206, pipeline/filter.py:501,
    builders/vector/build_index.py:40-42, builders/graph/load_to_neo4j.py:31-33,
    builders/unified_builder.py:620-622.
  What both claim to own: Each module reads its own os.getenv("...") directly. No single

    config object validates at startup.
  Impact: Misconfiguration is discovered only when the stage runs and fails. Tier-1: a
    missing NEO4J_PASSWORD is found after triple extraction has completed (cost: an LLM
    run).
  ────────────────────────────────────────
  #: O5
  Conflict: OPENROUTER_API_KEY specifically
  Files: builders/post_processor.py:35, builders/post_processor.py:573 (read twice in
  the
     same file), builders/vector/build_index.py:40, chronograph/config.py:18, commented
    in pipeline/triple_extractor.py:1090.
  What both claim to own: Same env var read by 4+ unrelated locations. No single place
    documents who uses it.
  Impact: If you rotate the key, you must update… nothing (env), but you also can't tell

    which stages will fail to authenticate without running them.
  ────────────────────────────────────────
  #: O6
  Conflict: KnowledgeEvent schema
  Files: common/schemas.py defines it; connectors/gmail/normalizer.py populates it;
    pipeline/filter.py and pipeline/person_registry.py read it as raw dicts without
    re-validating.
  What both claim to own: The schema is defined once but never enforced on read. After
    Stage 2, the dataclass is dropped — downstream stages treat the JSONL as untyped
    dict.
  Impact: Schema drift goes undetected until LLM extraction (Stage 5) gets a field that
    isn't there. Stage-2/3/4 don't fail-fast on schema breakage.
  ────────────────────────────────────────
  #: O7
  Conflict: Doc/code drift
  Files: lore-ingestion/CLAUDE.md (and root CLAUDE.md) reference
    builders/graph/generate_chunk.py, builders/graph/utils/assign_node_ids.py,
    builders/graph/utils/build_ontology_summary.py,
    builders/graph/utils/run_name_merge.py, builders/graph/dataset/. None of these
  exist
     on disk.
  What both claim to own: Stale documentation claims authority over a structure that has

    since been collapsed into builders/unified_builder.py.
  Impact: A new contributor will spend time looking for files that no longer exist. The
    doc is wrong; the code is right.

  4. Question 2 — Where Does Feedback Live?

  The logging discipline is actually good: common.logger.get_logger() is used uniformly
  in every Gmail-side module. Structured info logs at most stage boundaries. The gaps
  are concentrated in specific functions and at silent fall-back paths.

  #: F1
  Gap: Corrupt checkpoint silently restarts
  File:Line: pipeline/embed_knowledge.py:158-160, pipeline/triple_extractor.py:649-651
  Symptom if it fires: log.warning + fresh start. No raise, no exit code. If a
  checkpoint
     is corrupt on a 30-hour LLM run, you re-pay $X without ever seeing an alert. Should

    require explicit --force-restart flag.
  ────────────────────────────────────────
  #: F2
  Gap: Network errors swallowed in batch
  File:Line: connectors/gmail/fetcher.py:401-414, connectors/gmail/fetcher.py:466-512,
    connectors/gmail/fetcher.py:578
  Symptom if it fires: Multiple except _NETWORK_ERRORS / except Exception blocks. The
    fetcher continues to the next user. No persisted "failed-user  list" I can see — if
    user bob@ keeps 429-ing, the operator only sees logs scroll, no summary at end.
  ────────────────────────────────────────
  #: F3
  Gap: Normalizer skips bad emails silently
  File:Line: connectors/gmail/normalizer.py:42-45, :96-99
  Symptom if it fires: except Exception as e: continue-style. Counts not surfaced in a
    final report. Silent truncation — if 5% of emails fail to parse, you discover it
  only
     by comparing counts manually.
  ────────────────────────────────────────
  #: F4
  Gap: pipeline/filter.py:316
  File:Line: Bare except Exception as e in LLM classifier path
  Symptom if it fires: If --skip-llm is off and the Anthropic call fails, the email gets

    default-classified (department=unknown). No metric for "% of events that fell back
  to
     default" is logged at end of run.
  ────────────────────────────────────────
  #: F5
  Gap: Empty-output stages have no gate
  File:Line: All pipeline stages
  Symptom if it fires: No stage has an assertion like assert len(output) >
    expected_lower_bound. If filter outputs 0 events because of a regex bug, Stage 3
  just
     runs on 0 inputs and produces an empty registry. The pipeline reports "success" 6
    times.
  ────────────────────────────────────────
  #: F6
  Gap: Validator rejects without summary log
  File:Line: pipeline/validator.py
  Symptom if it fires: Produces _triples_rejected.json (audit trail — good!) but I see
  no
    log line at end like "validated 9,213 / rejected 2,108 (18.6%)". Operator must wc -l

    the JSON to know rejection rate. (Need to verify by reading the bottom of the file —

    flagging as a likely gap.)
  ────────────────────────────────────────
  #: F7
  Gap: builders/post_processor.py:184 bare except Exception as e in topic clustering LLM

    labeling
  File:Line: If LLM labeling fails, topics get fallback names. No count of fallback
    labels surfaced.
  Symptom if it fires: Quality silently degrades.
  ────────────────────────────────────────
  #: F8
  Gap: builders/unified_builder.py:439 bare except ImportError
  File:Line: If neo4j driver is missing, falls back to writing Cypher files only. Good
    behavior, but no log warning at startup that you are in "no-upload" mode unless you
    read the bottom of the run.
  Symptom if it fires:
  ────────────────────────────────────────
  #: F9
  Gap: No startup config validation
  File:Line: All entry points
  Symptom if it fires: None of the CLI entries (__main__ blocks in
    connectors/gmail/run.py, all six pipeline/*.py, builders/unified_builder.py)
  validate
     that required env vars are present before doing work. Failures happen mid-pipeline.
  ────────────────────────────────────────
  #: F10
  Gap: Three retries inside triple_extractor, but final failures only logged per-thread
  File:Line: pipeline/triple_extractor.py:752-792
  Symptom if it fires: After 3 failed retries, the thread is dropped. There is
  per-thread
    logging but no end-of-run summary like "7 of 14,213 threads permanently failed:
    [thread_ids…]".

  Net: Logging exists, but it is local. Feedback that should live at stage boundaries
  (counts, rejection rates, fall-back rates, network failure tallies) is missing.

  5. Question 3 — Blast Radius Table

  Module: common/logger.py
  Direct callers (intra-repo): ~20 files
  Risk: HIGH
  Why: Used everywhere. API change breaks all logs. But interface is stable and trivial
  —
    risk is theoretical.
  ────────────────────────────────────────
  Module: common/hashing.py
  Direct callers (intra-repo): validator.py, post_processor.py, normalizer.py,
    unified_builder.py (indirectly)
  Risk: HIGH
  Why: Changing short_hash() invalidates every node in Neo4j and every doc in the vector

    index. No version stamp on the hashing algorithm.
  ────────────────────────────────────────
  Module: common/schemas.py (KnowledgeEvent)
  Direct callers (intra-repo): connectors/gmail/normalizer.py, connectors/gmail/run.py
    produce it; pipeline reads as dicts
  Risk: MEDIUM
  Why: Producers are typed; consumers are not. Adding a field is safe; renaming is
    dangerous because downstream uses string keys.
  ────────────────────────────────────────
  Module: common/schemas_extraction.py (Triple, ExtractionResult, Predicate, NodeType)
  Direct callers (intra-repo): triple_extractor.py, validator.py,
    knowledge_distillation/distill.py, scripts/model_testing.py
  Risk: HIGH
  Why: Enum changes here flow through LLM prompts and validator. Adding a new predicate
    requires coordinated edits in 4 places.
  ────────────────────────────────────────
  Module: common/output_paths.py
  Direct callers (intra-repo): 9+ files
  Risk: MEDIUM
  Why: Centralized → safe to refactor. Path additions are zero-risk; removals would
    cascade.
  ────────────────────────────────────────
  Module: common/rbac.py
  Direct callers (intra-repo): validator.py, schemas_extraction.py,
  vector/build_index.py
  Risk: MEDIUM
  Why: Department list is duplicated knowledge — used by both LLM prompts and validator.

    Adding a department requires touching prompt strings in triple_extractor.py
    separately.
  ────────────────────────────────────────
  Module: common/org_config.py
  Direct callers (intra-repo): gmail/run.py, filter.py, person_registry.py,
  comm_graph.py
  Risk: MEDIUM
  Why: Schema additions safe; removals break ingestion.
  ────────────────────────────────────────
  Module: pipeline/validator.py
  Direct callers (intra-repo): triple_extractor.py, knowledge_distillation/distill.py,
    scripts/model_testing.py
  Risk: HIGH
  Why: Imported as a library, not just run as a stage. Function signatures
    (validate_thread, build_name_lookup) are public contract.
  ────────────────────────────────────────
  Module: pipeline/triple_extractor.py
  Direct callers (intra-repo):
  knowledge_distillation/{allocator,build_sft_dataset,distill}.py,
    scripts/model_testing.py
  Risk: HIGH
  Why: Same: imported as a library. Prompt builders and thread-grouping logic are public

    surface area.
  ────────────────────────────────────────
  Module: pipeline/embed_knowledge.py
  Direct callers (intra-repo): pipeline/deduplicator.py
  Risk: LOW
  Why: One caller, narrow interface.
  ────────────────────────────────────────
  Module: builders/unified_builder.py
  Direct callers (intra-repo): None (entry point)
  Risk: LOW for changes; HIGH for output
  Why: Internal refactor is safe — but its outputs (Cypher files, embeddings) are the
    contract with lore-backend.
  ────────────────────────────────────────
  Module: chronograph/*
  Direct callers (intra-repo): Self-contained
  Risk: LOW
  Why: Isolated subtree. Could be extracted to its own repo with zero impact on Gmail
    pipeline.
  ────────────────────────────────────────
  Module: knowledge_distillation/*
  Direct callers (intra-repo): Self-contained (consumes pipeline)
  Risk: LOW
  Why: Experimental. Could be deleted without affecting production.
  ────────────────────────────────────────
  Module: evaluation/*
  Direct callers (intra-repo): Self-contained
  Risk: LOW
  Why: QA only; no production dependency.

  Highest unmarked risk: common/hashing.py is HIGH-blast-radius but has no version
  field. A change in short_hash is invisible to consumers.

  6. Named Structural Problems

  1. Scattered Configuration ("Env Var Shotgun")
    - Where: os.getenv calls in 12+ files. Only chronograph/config.py does it properly.
    - Impact: No startup validation. Misconfiguration discovered mid-pipeline. No single
   source of truth for what env vars exist.
    - Root cause: Grew organically. chronograph/config.py proves the team knows the
  right pattern but didn't apply it across.
  2. Duplicated Node-ID Generation
    - Where: pipeline/validator.py:697-709 ↔ builders/post_processor.py:281, 340, 357,
  368.
    - Impact: Risk of Neo4j ↔ vector-index ID divergence. Silently wrong joins.
    - Root cause: common/hashing.py provides short_hash (primitive) but no
  node_id_for(type, name) helper, so each caller reimplements.
  3. Two Embedding Models, One Pipeline
    - Where: pipeline/embed_knowledge.py (NVIDIA NeMo 1024-dim) and
  builders/vector/build_index.py (OpenAI 1536-dim + local fallback).
    - Impact: Backend semantic search depends on which command was run. Vector dimension
   mismatch produces a runtime error at best, wrong results at worst.
    - Root cause: Two builders evolved in parallel; no shared embedding registry.
  Compare to lore-backend's infra/embeddings/ provider registry — that pattern is
  missing here.
  4. Duplicated Cypher Escaping
    - Where: builders/unified_builder.py:37-40 vs builders/post_processor.py:38-41.
  Different implementations of the same function.
    - Impact: Cypher injection / batch-split bugs in one of the two paths.
    - Root cause: No builders/cypher_writer.py shared helper.
  5. God Modules
    - Where: pipeline/triple_extractor.py (1273 LOC), pipeline/validator.py (755),
  pipeline/filter.py (686), pipeline/person_registry.py (692),
  builders/unified_builder.py (629), pipeline/deduplicator.py (587),
  builders/post_processor.py (583), connectors/gmail/fetcher.py (589).
    - Inside each: mixed concerns — triple_extractor.py contains: rate limiter, system
  prompt (~hundreds of lines), thread grouping, LLM call, retry+validation,
  checkpointing, CLI. Each is its own responsibility.
    - Impact: Cannot test components in isolation. Cannot reuse the prompt (e.g., from
  knowledge_distillation) without dragging in the whole module — which is why
  knowledge_distillation/distill.py re-imports the entire extractor as a library.
    - Root cause: No internal sub-package boundaries inside pipeline/.
  6. Silent Stage Boundaries
    - Where: No stage has a "minimum expected output" sanity check (F5). Failure modes
  detailed in §4.
    - Impact: Zero-output stages pretend to succeed. End-of-run summaries (rejected %,
  network-failed users, dropped threads) live in JSON files, not logs.
  7. Documentation Drift
    - Where: CLAUDE.md references builders/graph/generate_chunk.py,
  builders/graph/utils/*, builders/graph/dataset/ — none exist.
    - Impact: Contributors waste time. The doc misrepresents the system.
    - Root cause: A builders/graph/ refactor consolidated into unified_builder.py but
  CLAUDE.md was not updated.
  8. Chronograph is an Architectural Island
    - Where: chronograph/* shares zero code with the Gmail pipeline. Has its own
  config.py, entities.py, logging.
    - Impact: Either chronograph should be extracted to its own repo OR it should join
  the common/ layer like Gmail does.
    - Root cause: Bolted on as a separate concern (GitLab code intelligence).
  9. Copy-Paste Teacher Models
    - Where: knowledge_distillation/models/{glm,kimi,minimax,nemotron,nemotron_super,ns3
  _0,ns3_1,ns3_2,qwen,qwen_openrouter}_nvidia.py — 10 files, 33-36 lines each, differing
   only by api_base_url / model_id / api_key env var name.
    - Impact: Adding an 11th model = copying a file. Drift between siblings (some have
  fallback logic, some don't).
    - Root cause: No registry pattern; each model is a class instead of a config record.
  10. Imported-as-Library vs Run-as-Stage Confusion
    - Where: pipeline/triple_extractor.py and pipeline/validator.py are both CLI entry
  points AND imported as libraries (by knowledge_distillation/*,
  scripts/model_testing.py).
    - Impact: The line between "this is a stage" and "this is a function I expose" is
  invisible. Any refactor of the stage breaks the library users.
    - Root cause: No explicit public-API split (no pipeline/extraction/__init__.py
  re-exports, no __all__).

  7. Proposed Architecture (4 Layers, Adapted)

  ┌─────────────────────────────────────────────────────────────────────┐
  │ LAYER 0 — CORE (no internal imports; pure data + utilities)         │
  │   common/                                                            │
  │     config.py            ← NEW. Single Pydantic Settings class.      │
  │                            All env vars read once at process start.  │
  │     schemas/                                                         │
  │       events.py          ← was schemas.py (KnowledgeEvent, Actor)    │
  │       extraction.py      ← was schemas_extraction.py                 │
  │       rbac.py            ← unchanged                                 │
  │     hashing.py           ← add node_id_for(type, name, scope=None)   │
  │                            with HASH_VERSION constant.               │
  │     output_paths.py      ← unchanged                                 │
  │     logger.py            ← unchanged                                 │
  │     org_config.py        ← unchanged                                 │
  │                                                                     │
  ├─────────────────────────────────────────────────────────────────────┤
  │ LAYER 1 — INFRASTRUCTURE (third-party clients, IO)                  │
  │   infra/                                                             │
  │     llm/                                                             │
  │       base.py            ← ChatProvider Protocol                     │
  │       openrouter.py      ← one place that talks to openrouter.ai     │
  │       nvidia.py          ← one place that talks to nvidia API        │
  │       anthropic.py       ← (used by pipeline/filter.py today)        │
  │       registry.py        ← get_chat_client() from env                │
  │     embeddings/                                                      │
  │       base.py            ← EmbeddingProvider Protocol                │
  │       openrouter.py      ← text-embedding-3-small                    │
  │       nvidia.py          ← nemoretriever-300m                        │
  │       local.py           ← sentence-transformers fallback            │
  │       registry.py        ← get_embedder() from env                   │
  │     gmail/                                                           │
  │       client.py          ← was connectors/gmail/fetcher.py           │
  │     neo4j/                                                           │
  │       writer.py          ← was builders/graph/load_to_neo4j.py       │
  │       cypher.py          ← shared _esc() and Cypher emitters         │
  │     storage/                                                         │
  │       checkpoint.py      ← extracted from triple_extractor +         │
  │                            embed_knowledge. ONE checkpoint impl.     │
  │                                                                     │
  ├─────────────────────────────────────────────────────────────────────┤
  │ LAYER 2 — DOMAIN (stages as pure functions, no IO except via L1)    │
  │   ingest/                                                            │
  │     gmail/                                                           │
  │       fetch.py           ← orchestrates infra.gmail.client           │
  │       normalize.py       ← raw MIME → KnowledgeEvent                 │
  │     filter/              ← was pipeline/filter.py, split:            │
  │       signals.py         ← rule-based heuristics                     │
  │       classifier.py      ← LLM department classifier                 │
  │     identity/                                                        │
  │       registry.py        ← was pipeline/person_registry.py           │
  │     graph/                                                           │
  │       comm.py            ← was pipeline/comm_graph.py                │
  │     extraction/                                                      │
  │       prompts.py         ← prompt strings ONLY (today: 200+ lines    │
  │                            inside triple_extractor.py)               │
  │       grouping.py        ← thread-size bucket logic                  │
  │       rate_limit.py      ← was AsyncRateLimiter                      │
  │       extract.py         ← the async extraction loop ONLY            │
  │     validation/                                                      │
  │       schema_rules.py    ← was top half of validator.py              │
  │       content_rules.py   ← was middle of validator.py                │
  │       name_resolver.py   ← was rapidfuzz person checks               │
  │       node_ids.py        ← node_id assignment (one owner)            │
  │     embed/                                                           │
  │       embed.py           ← was pipeline/embed_knowledge.py           │
  │     dedupe/                                                          │
  │       dedupe.py          ← was pipeline/deduplicator.py              │
  │     build/                                                           │
  │       graph_writer.py    ← Cypher emission (was unified_builder)     │
  │       vector_writer.py   ← was builders/vector/build_index.py        │
  │       post_process.py    ← was builders/post_processor.py            │
  │                                                                     │
  ├─────────────────────────────────────────────────────────────────────┤
  │ LAYER 3 — ORCHESTRATION (CLI entry points; sequence stages)         │
  │   runners/                                                           │
  │     gmail_fetch.py       ← thin CLI over ingest/gmail/fetch.py       │
  │     filter.py            ← thin CLI                                  │
  │     person_registry.py                                               │
  │     comm_graph.py                                                    │
  │     extract.py                                                       │
  │     validate.py                                                      │
  │     embed.py                                                         │
  │     dedupe.py                                                        │
  │     build.py             ← runs the database builders                │
  │     full_pipeline.py     ← NEW. End-to-end runner with --resume      │
  │   evaluation/            ← unchanged structure                       │
  │                                                                     │
  ├─────────────────────────────────────────────────────────────────────┤
  │ SIDECARS (not in main flow; depend on Layers 0-2)                   │
  │   chronograph/           ← becomes a peer of ingest/ — gradually     │
  │                            adopt common/ infrastructure              │
  │   knowledge_distillation/← experimental; isolated, imports L2 only   │
  └─────────────────────────────────────────────────────────────────────┘

  How each principle is satisfied

  - Single ownership:
    - Hashing → common/hashing.py:node_id_for() (one formula).
    - Cypher escape → infra/neo4j/cypher.py:_esc() (one impl).
    - Embedding model → infra/embeddings/registry.py (one selector, env-driven).
    - Config → common/config.py:Settings (one Pydantic model, validated at process
  start).
  - Observability at boundaries: every runner in Layer 3 ends with a structured summary
  line: stage=extract input=N output=M rejected=X retried=Y dropped=Z duration=Ts.
  - Explicit contracts: Pydantic models cross every Layer 2 boundary. No raw dicts.
  - Imports inward only: Layer 2 may import L0/L1 but never L3. Library users
  (knowledge_distillation) import from L2 only — they no longer reach into a stage
  runner.

  8. Migration Plan

  Every step is independently shippable and reversible. Order is: cleanup → consolidate
  → split → restructure.

  ---
  Step 1 — Delete dead doc, fix stale paths in CLAUDE.md
  - Scope: lore-ingestion/CLAUDE.md, root CLAUDE.md. Remove references to nonexistent
  builders/graph/generate_chunk.py, builders/graph/utils/*, builders/graph/dataset/.
  - Touches: docs only.
  - Risk: LOW.
  - Test: grep -r "generate_chunk" . returns empty.
  - Rollback: git revert.

  Step 2 — Centralize Cypher _esc()
  - Scope: create builders/_cypher.py with one _esc().      Update unified_builder.py:37-40 and post_processor.py:38-41 to import from it. 
  Pick the stricter version (the one that also strips ;).
  - Touches: 2 callers.
  - Risk: LOW (pure-function consolidation).
  - Test: regenerate a Cypher file, diff against last known-good output for one org. Must be byte-identical except for the ;-handling fix.
  - Rollback: revert.

  Step 3 — Move node-ID formula into common/hashing.py
  - Scope: add def node_id_for(node_type: str, name: str, scope: str = "") -> str and
  HASH_VERSION = "v1" to common/hashing.py. Replace inline calls in validator.py:697-709
   and post_processor.py:281, 340, 357, 368.
  - Touches: 2 files.
  - Risk: LOW if formula is preserved exactly. MEDIUM if any inputs differ (e.g., one
  lowercases name, the other doesn't — check carefully).
  - Test: pick 100 thread IDs, generate node IDs both before and after; must match
  byte-for-byte.
  - Rollback: revert.

  Step 4 — Add common/config.py Settings module (Pydantic)
  - Scope: new file. Define all env vars in one Settings(BaseSettings). Do NOT remove
  existing os.getenv calls yet — just add the Settings object and a fail-fast
  validate_required() function for each runner's prerequisites.
  - Touches: new file only. CLI entry points get one new line: from common.config import
   settings; settings.validate_for("triple_extractor").
  - Risk: LOW. Behavior identical until validation triggers a missing key.
  - Test: run each stage with all env vars present → succeeds. With one removed → fails
  at startup, not mid-pipeline.
  - Rollback: delete file, remove validation lines.

  Step 5 — Migrate os.getenv callsites to Settings
  - Scope: replace os.getenv("OPENROUTER_API_KEY", "") etc. with
  settings.openrouter_api_key. Cover pipeline/*.py, builders/*.py,
  connectors/gmail/run.py.
  - Touches: ~12 files.
  - Risk: LOW (mechanical).
  - Test: each stage runs with no behavior change.
  - Rollback: revert.

  Step 6 — Choose one canonical embedding model; deprecate the others
  - Scope: this is a decision step, not just code. Pick: NVIDIA NeMo (1024-dim, used by
  embed_knowledge.py) OR OpenAI text-embedding-3-small (1536-dim, used by
  vector/build_index.py). Mark the other code path with a deprecation warning. Update
  both writers to read the model name from settings.embedding_model.
  - Touches: pipeline/embed_knowledge.py, builders/vector/build_index.py,
  builders/post_processor.py:69 (also embeds via OpenRouter).
  - Risk: HIGH if you flip the canonical model — the existing Neo4j vector index becomes
   wrong and must be rebuilt. LOW if you just centralize the name without changing the
  active model.
  - Test: confirm lore-backend vector search returns identical top-K for 20 fixed
  queries after rebuild.
  - Rollback: revert the canonical-model decision; rebuild index from old model.

  Step 7 — Add end-of-run summary logging to every stage
  - Scope: at the end of each runner, log a structured summary line. Specific gaps to
  close (from §4): F3 (normalizer rejected count), F6 (validator approved/rejected), F2
  (fetcher network-failed users list), F10 (extractor permanently-failed threads list).
  - Touches: gmail/run.py, pipeline/filter.py, pipeline/person_registry.py,
  pipeline/triple_extractor.py, pipeline/validator.py, pipeline/embed_knowledge.py,
  pipeline/deduplicator.py, builders/unified_builder.py.
  - Risk: LOW (additive only).
  - Test: run any stage; verify the summary appears in stdout.
  - Rollback: revert lines.

  Step 8 — Add minimum-output gates at stage boundaries
  - Scope: after each stage writes its artifact, assert len(output) > 0 (or a
  configurable threshold) and raise loudly with the actual count. This closes F5.
  - Touches: same 8 files as Step 7.
  - Risk: LOW (raises on what was previously "success with 0 rows" — caught early is
  correct).
  - Test: artificially empty an input; verify next stage fails fast.
  - Rollback: revert.

  Step 9 — Replace silent checkpoint corruption with explicit recovery
  - Scope: pipeline/embed_knowledge.py:158-160 and pipeline/triple_extractor.py:649-651
  currently warn-and-restart. Change to: log error, exit non-zero by default. Add
  --force-restart flag to opt into the current behavior.
  - Touches: 2 files.
  - Risk: LOW.
  - Test: corrupt a checkpoint manually; verify the run fails with a clear message;
  verify --force-restart makes it succeed.
  - Rollback: revert.

  Step 10 — Split pipeline/triple_extractor.py (1273 LOC) into subpackage
  - Scope: create pipeline/extraction/ with prompts.py (system prompt + user-prompt
  builder), rate_limit.py (AsyncRateLimiter), grouping.py (thread-size bucket logic),
  extract.py (async loop + retry + self-correction), cli.py (argparse + entry point).
  - Move imports; preserve public surface (knowledge_distillation/* imports several
  symbols from triple_extractor).
  - Add pipeline/extraction/__init__.py re-exporting the public symbols so external
  imports keep working.
  - Touches: pipeline/triple_extractor.py,
  knowledge_distillation/{allocator,build_sft_dataset,distill}.py,
  scripts/model_testing.py.
  - Risk: MEDIUM. Library callers can break if __init__.py doesn't re-export everything.
  - Test: import all symbols used by callers in a smoke script; run
  knowledge_distillation/distill.py --help; run python -m pipeline.extraction.cli
  --help.
  - Rollback: revert; the monolith returns.

  Step 11 — Split pipeline/validator.py (755 LOC) into subpackage
  - Same pattern as Step 10. Create
  pipeline/validation/{schema_rules,content_rules,name_resolver,node_ids,cli}.py.
  - Move node_id assignment logic into node_ids.py, which is the canonical place that
  calls common.hashing.node_id_for.
  - Risk: MEDIUM (knowledge_distillation/distill.py imports validate_thread,
  build_name_lookup directly).
  - Test: same as Step 10.
  - Rollback: revert.

  Step 12 — Introduce infra/llm/ and infra/embeddings/ provider registries
  - Scope: borrow the pattern from lore-backend/infra/llm/. One Protocol, one impl per
  provider, one registry that picks based on settings.
  - Replace the inline AsyncOpenAI(...) constructions in
  pipeline/extraction/extract.py:1106, builders/post_processor.py:148,
  pipeline/embed_knowledge.py:206, builders/vector/build_index.py:68.
  - Touches: 4 files + new subpackage.
  - Risk: MEDIUM (changes how clients are constructed; auth could break if env wiring is
   off).
  - Test: each affected stage runs end-to-end on a small --limit 5 slice.
  - Rollback: revert.

  Step 13 — Collapse knowledge_distillation/models/* into a registry
  - Scope: replace 10 near-identical files with one models.yaml (or models.py data
  table) and a single Teacher class parameterized by the table. Reduces ~340 LOC to ~80.
  - Touches: knowledge_distillation/models/, knowledge_distillation/distill.py.
  - Risk: LOW (experimental subsystem; not on production path).
  - Test: python -m knowledge_distillation.distill --help works; smoke test one teacher.
  - Rollback: revert.

  Step 14 — Rename connectors/ → infra/gmail/ (final shape)
  - Scope: physical move. Update imports.
  - Risk: LOW (mechanical).
  - Test: pipeline runs.
  - Rollback: revert.

  Step 15 — Introduce runners/ with thin CLI wrappers
  - Scope: each pipeline/<stage>.py __main__ block becomes a runners/<stage>.py file
  that imports from the now-pure domain code. The old python -m pipeline.X paths keep
  working via pipeline/X.py becoming a one-liner that delegates.
  - Touches: each stage entry point.
  - Risk: LOW.
  - Test: every command in CLAUDE.md still runs.
  - Rollback: revert.

  Step 16 — (Optional, larger) Pull chronograph/ into common/ infrastructure
  - Scope: replace chronograph/config.py with common/config.py; drop
  chronograph/entities.py in favor of common/schemas/; use common/logger.py.
  - Risk: MEDIUM. Chronograph has its own MongoDB writer that may have field-name
  assumptions.
  - Test: run chronograph end-to-end on a small repo.
  - Rollback: revert.

  ---
  9. What This Gives You

  Who owns the truth?
  - Configuration → common/config.py:Settings. Read once at process start, validated,
  immutable.
  - Schemas → common/schemas/ is the only place data shapes are defined.
  - Hashing → common/hashing.py:node_id_for() is the only place IDs are minted.
  - Cypher escaping → infra/neo4j/cypher.py:_esc().
  - Embeddings → infra/embeddings/registry.py. One model per process, name in settings.
  - LLM provider → infra/llm/registry.py. One ChatProvider per process.
  - Prompts → pipeline/extraction/prompts.py. Reusable by knowledge_distillation without
   dragging in the runner.

  Where does feedback live?
  - Every runner emits one structured summary line at end: input N, output M, rejected
  X, dropped Y, retried Z, duration.
  - Every stage asserts len(output) > 0 (or configurable lower bound) before declaring
  success.
  - Checkpoint corruption is loud, not silent — exits non-zero by default.
  - Network failures in gmail/client.py accumulate into a failed_users.json artifact,
  surfaced in the end-of-run log.
  - LLM extraction permanently-failed threads accumulate into failed_threads.json.
  - Config validation happens at process start, before any IO or LLM cost is incurred.

  What breaks if I delete this?
  - Delete a Layer-3 runner → that CLI stops working; nothing else changes.
  - Delete a Layer-2 domain module → its runner breaks, AND any direct importer
  (knowledge_distillation, evaluation) breaks. Now obvious from __init__.py re-exports.
  - Delete a Layer-1 infra module → all dependent domain stages fail at startup
  (provider not found). Loud, immediate failure.
  - Delete a Layer-0 core module → the world burns immediately. Cannot be subtle,
  because every import chain leads here.
  - Delete chronograph/ or knowledge_distillation/ → Gmail pipeline unaffected.

  The blast radius is now visible from the directory tree.

  10. Model Handoff Recommendation

  ▎ Planning is done. The thinking-heavy work is finished.
  ▎
  ▎ You used Opus for this audit because mapping dependencies, tracing ownership
  ▎ conflicts across 30+ Python files, and designing the layered architecture required
  ▎ reasoning across the whole codebase at once.
  ▎
  ▎ For implementation, switch to a smaller, faster model (e.g., Claude Sonnet 4.6 or
  ▎ Haiku 4.5). Every step above is scoped, bounded, and self-contained. Steps 1–9 are
  ▎ mechanical. Steps 10–11 (module splits) are larger but well-specified. The
  ▎ implementation work is now execution against a known spec, not open-ended reasoning.
  ▎
  ▎ Switch with /model claude-sonnet-4-6 or /model claude-haiku-4-5 in Claude Code.
  ▎
  ▎ Come back to Opus only if Step 6 (embedding-model decision) or Step 16 (chronograph
  ▎ adoption) surfaces a structural surprise that requires re-evaluating the plan.

  ---
  Plan is complete and reviewable. Push back on any step before I touch code —
  particularly Step 6 (which embedding model is canonical?) and Step 10/11 (whether to
  preserve pipeline/triple_extractor.py as a re-export shim for backwards compatibility
  with knowledge_distillation).