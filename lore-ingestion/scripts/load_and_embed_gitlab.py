"""
load_and_embed_gitlab.py
────────────────────────
One-shot: load the cleaned gitlab_export/clean/*.json (table-row dumps) into the
chronograph.* Postgres tables, then embed function/method symbols into
chronograph.symbol_embeddings (pgvector) using the same NeMo model + text recipe
as chronograph's own poller, so the QueryEngine's semantic search works.

Idempotent: table loads use ON CONFLICT DO NOTHING; embeddings upsert.
Run in the ingestion venv (chronograph + common.config import there).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from chronograph.store.store_factory import make_store
from chronograph.store import models as M
from common.config import get_settings

CLEAN = Path("/home/zeiko/work/loremind/lore/lore-engine/gitlab_export/clean")

# file  ->  ORM model. Order respects FK-ish logical dependencies (repos first).
FILE_MODEL = [
    ("repos", M.Repo),
    ("members", M.Member),
    ("commits", M.Commit),
    ("commit_files", M.CommitFile),
    ("source_files", M.SourceFile),
    ("symbols", M.Symbol),
    ("edges", M.Edge),
    ("snapshots", M.Snapshot),
]


def load_table(store, name: str, model) -> tuple[int, int]:
    rows = json.loads((CLEAN / f"{name}.json").read_text(encoding="utf-8"))
    if not rows:
        return 0, 0
    # Columns the ORM table actually has; never insert the server-managed ts.
    cols = [c.name for c in model.__table__.columns if c.name != "updated_at"]
    colset = set(cols)
    # Normalize every row to the SAME key set (multi-row VALUES needs it).
    norm = [{c: r.get(c) for c in cols if c in r or c in colset} for r in rows]
    # Ensure identical keys across all dicts
    norm = [{c: r.get(c) for c in cols} for r in rows]

    inserted = 0
    with store._session() as s:
        for i in range(0, len(norm), 500):
            chunk = norm[i:i + 500]
            stmt = pg_insert(model).values(chunk).on_conflict_do_nothing()
            inserted += s.execute(stmt).rowcount
    return len(norm), inserted


def embed_symbols(store, settings, project_id: str) -> dict:
    from openai import OpenAI

    symbols = store.get_symbols(project_id)
    to_embed = [
        s for s in symbols
        if s.get("kind") in ("function", "method")
        and s.get("qualified_name")
        and ".min." not in s.get("file_path", "")
        and "/public/js/" not in s.get("file_path", "")
    ]
    print(f"  symbols total={len(symbols)}  embeddable(function/method)={len(to_embed)}")

    client = OpenAI(api_key=settings.nvidia_api_key, base_url=settings.nvidia_base_url)
    model = settings.embedding_model

    texts, metas = [], []
    for sym in to_embed:
        t = f"{sym['qualified_name']} {sym.get('signature', '')} {sym.get('file_path', '')}"
        if sym.get("params"):
            t += f" params: {' '.join(sym['params'])}"
        if sym.get("calls"):
            t += f" calls: {' '.join(sym['calls'][:10])}"
        texts.append(t)
        metas.append(sym)

    embeddings, batch = [], 50
    for i in range(0, len(texts), batch):
        bt = texts[i:i + batch]
        resp = client.embeddings.create(
            input=bt, model=model, encoding_format="float",
            extra_body={"input_type": "passage", "truncate": "END"},
        )
        for j, item in enumerate(resp.data):
            sym = metas[i + j]
            embeddings.append({
                "symbol_id": sym["symbol_id"],
                "qualified_name": sym["qualified_name"],
                "file_path": sym["file_path"],
                "kind": sym["kind"],
                "text": bt[j][:500],
                "vector": item.embedding,
            })
        print(f"  embedded {min(i + batch, len(texts))}/{len(texts)} "
              f"(dim={len(resp.data[0].embedding)})")
        if i + batch < len(texts):
            time.sleep(settings.embed_delay_s)

    stored = store.upsert_embeddings(project_id, embeddings)
    return {"embedded": len(embeddings), "stored": stored, "model": model}


def main() -> int:
    settings = get_settings()
    store = make_store(settings)
    assert store.connect(), "store.connect() failed"

    print("── Loading tables ──")
    for name, model in FILE_MODEL:
        total, inserted = load_table(store, name, model)
        print(f"  {name:14} {inserted}/{total} inserted")

    # discover the project id from the loaded symbols
    with store._session() as s:
        project_id = s.execute(
            text("SELECT project_id FROM chronograph.repos LIMIT 1")
        ).scalar()
    print(f"\nproject_id = {project_id}")

    print("\n── Embedding symbols (NVIDIA NeMo, input_type=passage) ──")
    result = embed_symbols(store, settings, project_id)
    print(f"  -> {result}")

    print("\n── Store stats ──")
    print(json.dumps(store.get_stats(project_id), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
