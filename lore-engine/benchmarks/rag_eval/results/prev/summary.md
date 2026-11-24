## Retrieval quality (top-10), by ontology pattern

| System | person_tasks | decision_why | person_topic | ALL | median ms |
|---|---|---|---|---|---|
| raw_rag — thread recall@10 | 0.06 | 0.82 | 0.04 | 0.33 | 316 |
| node_rag — thread recall@10 | 0.16 | 0.82 | 0.02 | 0.35 | 313 |
| lore — thread recall@10 | 0.29 | 0.82 | 0.05 | 0.40 | 7227 |
| node_rag — node recall@10 | 0.03 | 0.35 | 0.01 | 0.14 | 313 |
| lore — node recall@10 | 0.20 | 0.70 | 0.01 | 0.31 | 7227 |

## Paired bootstrap (95% CI) of Lore minus baseline, node recall@10

| Comparison | scope | mean diff | 95% CI |
|---|---|---|---|
| lore − node_rag | person_tasks (n=15) | +0.17 | [+0.09, +0.26] |
| lore − node_rag | decision_why (n=20) | +0.35 | [+0.10, +0.60] |
| lore − node_rag | person_topic (n=20) | +0.00 | [+0.00, +0.00] |
| lore − node_rag | ALL (n=55) | +0.17 | [+0.08, +0.27] |

## How the mail data is connected in Neo4j (live counts)

| Fact | Value |
|---|---|
| email threads that produced knowledge | 11,584 |
| knowledge nodes (Decision/Task/Fact/Event) | 27,063 |
| Person nodes (shared across threads) | 13,562 |
| Topic nodes (shared across threads) | 3,609 |
| typed relationships | 85,392 |
| Tasks with an ASSIGNED_TO person | 4,631 |
| Decisions with a BECAUSE fact | 4,658 |
| median threads touched per internal person | 16 |
| persons linked to ≥10 distinct threads | 100 |
