# Progress Report: Lore, a GraphRAG Knowledge Engine for Company Email

**Course:** CSE 4011, Intelligent Developer Tools and AI DevOps Workflows · **Phase 2 (mid-term)** · **Date:** 7 October 2026
**Author:** Ninad Wakode · **Repository:** `loremind-forge/ai-services/lore-engine`, branch `course/aidevops-midterm`

## 1. Objective and research question

Lore turns a company's email, chat and tickets into a typed knowledge graph and answers questions over it. For this mid-term we test one question: **does an ontology-typed knowledge graph retrieve better than plain vector RAG over company email, and where does it fail?**

We state three hypotheses. **H1:** the graph helps when the answer's text does not contain the entity asked about, because the relation lives on an edge. **H2:** the graph does not help on single-hop lookups. **H3:** the graph costs latency. These follow the literature: Lewis et al. 2020 (RAG), Edge et al. 2024 (GraphRAG), and Han et al. 2025 (RAG wins single-hop, GraphRAG wins multi-hop).

## 2. What has been built

| Area | Delivered |
|---|---|
| **Ingestion** | Separate pipelines per source: Gmail and Slack (filter, identity resolution, LLM triple extraction, validation), Jira (deterministic field extractor), code (GitLab commits and symbols). Each node carries ACL `scope_ids`. |
| **Ontology and graph** | 7 node types (Person, Organization, Topic, Decision, Task, Fact, Event) and typed edges (DECIDED, BECAUSE, ASSIGNED_TO, REGARDING and others) in Neo4j. Live graph from EZO Books email: **11,584 threads, 27,063 knowledge nodes, 13,562 people, 85,392 relationships.** |
| **Query engine** | FastAPI service: LLM query analysis, vector seeds, graph expansion over typed edges, FlashRank rerank, token-budgeted context, streamed LLM answer with cited sources. Two diagrams document the ingestion and query pipelines. |
| **RAG chatbot** | Working "Ask Lore" chat UI over the engine with source citations. It also answers GitLab code questions (1,840 commits, 160 files). One command, `scripts/demo_up.sh`, starts everything. |
| **CI/CD** | GitLab CI pipeline on the shared templates: lint, security scans, unit-test gate (65% coverage floor), image build to ECR, staging deploy, manual production promote. GitOps delivery through ArgoCD to the `loremind-eks` cluster. Terraform covers the AWS estate. Latest course-branch pipeline: #1426, success. |
| **AI test generation** | `tools/gen_tests.py` asks an LLM for tests per module, runs them, and keeps them only when coverage rises. |

## 3. Results

**Evaluation method.** 55 questions are generated from the graph itself in three ontology patterns, each with a Cypher-derived ground truth. We compare raw-email RAG, node-level vector RAG (LangChain + FAISS) and Lore. We report recall@10 with a paired bootstrap 95% confidence interval (5,000 resamples).

| Node recall@10 | person_tasks (n=15) | decision_why (n=20) | person_topic (n=20) | All (n=55) |
|---|---|---|---|---|
| Vector RAG over nodes | 0.03 | 0.35 | 0.01 | 0.14 |
| **Lore** | **0.20** | **0.70** | 0.01 | **0.31** |
| Difference, 95% CI | +0.17 [+0.09, +0.26] | +0.35 [+0.10, +0.60] | 0.00 | +0.17 [+0.08, +0.27] |

- **H1 supported.** Only 4% of correct task texts contain any part of the person's name, so text embeddings cannot find them. The graph follows the edge, recovering 6.7 times more correct tasks.
- **`decision_why`.** All systems find the right thread (recall 0.82), but Lore finds the justifying Fact twice as often by following BECAUSE edges.
- **H2 supported.** On single-thread lookups the vector baseline is as good or better.
- **H3 supported.** Median latency is about 7 s for Lore against 0.3 s for vector search, roughly 20 times slower.
- **AI test generation.** The generator took three modules from 41%, 41% and 62% coverage to 100%. It rejected a fourth after three attempts with no gain. The suite grew from 266 to 307 tests, and total coverage from 66% to 68% (306 passed, 1 skipped in a fresh run).

## 4. Problems found and limitations

- **`person_topic` fails for every system.** The person is found, but graph expansion starts at a hub with 978 neighbours and keeps only 30. The final rerank scores text similarity and ignores graph provenance, so the Person-and-Topic intersection is lost. This is a hypothesis from logs, not yet fixed or tested in an ablation.
- **Silent degradation.** Without the reranker installed and with an 800 ms analysis timeout, answers became generic. Both were fixed and are now pinned in the setup.
- **Evaluation limits.** One organisation, small samples per question family, templated questions that favour the structure the graph encodes, ground truth that is only as good as the LLM extraction, and no answer-quality score yet.
- **Tooling.** Sweep.dev works only on GitHub and the project uses GitLab, so GitLab CI replaces it. Qodo Cover is archived, so test generation uses our own tool with the same generate, run, keep-if-better method.
- **Deployment gap.** The engine image is built and its manifest is written, but it is not yet running on EKS. The graph database host is stopped, and the demo runs Neo4j locally.
- **Reproducibility.** The graph is private customer data, so questions are regenerated from the graph and no data is committed.

## 5. Revised timeline

| Date | Work | Status |
|---|---|---|
| 5 to 6 Oct | Re-embed graph, backfill scopes, evaluation harness, test generation, CI rewrite, chat UI, diagrams, course branch pushed | Done |
| 7 Oct | Confirm the unit-test gate on the new pipeline, fix `person_topic` anchoring, rerun the evaluation | Planned |
| 8 Oct | Add an LLM-judged answer-quality score, dry run of the demo with the chosen question set | Planned |
| 9 Oct | Presentation | Planned |
| After | Deploy the engine to EKS staging, restore a production UI | Out of scope for the mid-term |

## 6. Reproduce

```
cd lore-engine
scripts/demo_up.sh                                   # databases, engine, UI
python -m benchmarks.rag_eval.make_graph_questions   # regenerate questions
python -m benchmarks.rag_eval.run_graph_eval --systems node_rag lore
python -m benchmarks.rag_eval.report                 # writes results/summary.md
pytest tests -q --cov=domain --cov=services --cov=api
```

Full method, literature and failure analysis: `docs/MIDTERM_REPORT.md`.

## References

Lewis et al., 2020, arXiv:2005.11401 · Edge et al., 2024, arXiv:2404.16130 · Han et al., 2025, arXiv:2502.11371 · Barnett et al., 2024, arXiv:2401.05856 · arXiv:2511.05991 (ontology learning and knowledge-graph construction for RAG)
