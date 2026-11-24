# Lore: Ontology-Typed GraphRAG with an AI-Assisted DevOps Pipeline

Course project for AI DevOps (CSE 4011), BML Munjal University.

| Folder | What it is |
|---|---|
| `lore-ingestion/` | Email ingestion: fetch, filter, identity resolution, LLM triple extraction, validation, Neo4j graph build |
| `lore-engine/` | Query engine (FastAPI): graph frontier retrieval, rerank, grounded answer, conversation memory; CI in `.gitlab-ci.yml`, AWS demo in `deploy/oneday/` (Terraform) |
| `lore-engine/benchmarks/rag_eval/` | Evaluation: vector RAG vs Lore, ablation (`ablation.py`), bootstrap standard errors (`stderr.py`) |
| `lore-frontend/` | React/Vite UI |
| `paper/` | Research paper (LaTeX source, references, PDF) |

Not included: client email data, per-question evaluation outputs (they contain client staff names), secrets (`.env`), virtualenvs and build outputs.
