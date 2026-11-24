"""
domain/retrieval/
─────────────────
Three orthogonal retrieval strategies.

  person.py     — person name resolution + person-scoped decisions
  vector.py     — vector search + per-hit graph expansion
  expansion.py  — thread siblings + topic expansion

Each module takes a Neo4jClient and returns lists of KnowledgeNode dicts.
All Cypher templates live next to the function that uses them.
"""
