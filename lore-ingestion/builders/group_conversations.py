"""
builders/group_conversations.py
────────────────────────────────
Conversation grouping worker — build step 1d of 05_SYNC_AND_LEDGER.md.

Computes conversation membership as connected components over two edge sets that
the replica now retains (and the old JSONL pipeline threw away):

  1. reply-chain edges  — References / In-Reply-To link a message to its
                          ancestors by Message-ID (global, same in every inbox).
  2. sighting edges     — messages sharing a (mailbox, per-mailbox threadId) are
                          in the same Gmail thread; because one message can be
                          sighted in several mailboxes, these bridge threads
                          ACROSS mailboxes.

Together they stitch a conversation that is correct across mailboxes AND across
time — a reply arriving weeks later joins by header, with no in-RAM accumulator
(kills F10). This is the real fix for the 2.6% thread scatter that the
"oldest-message key" could not do: it uses actual co-membership, not a guessed
representative.

The result is a stable `conversation_id` per message, written onto lore.events.
Re-runnable: it is a pure function of the payload replica.

Usage (from lore-ingestion/):
    ../.venv/bin/python -m builders.group_conversations --org ezobooks [--source gmail]
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from builders.l0_ledger import LedgerStore, _resolve_dsn
from builders.normalize_events import _mailbox_of, _PROJECTORS
from common.hashing import make_id
from common.logger import get_logger, setup_logging
from infra.gmail.normalizer import reply_refs

log = get_logger("builders.group_conversations")


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def group_conversations(org: str, source: str = "gmail", batch: int = 500) -> dict:
    project = _PROJECTORS.get(source)
    if not project:
        raise ValueError(f"No normalizer for source {source!r} yet.")
    projector, _version = project

    store = LedgerStore(_resolve_dsn())
    if not store.connect():
        raise RuntimeError("group_conversations: could not connect to Postgres.")
    try:
        uf = _UnionFind()
        canon_ids: set[str] = set()
        threads: dict[tuple[str, str], list[str]] = defaultdict(list)
        # cid -> the REAL lore.events.id, per the same normalizer normalize_events
        # uses — must match exactly or the UPDATE below matches zero rows.
        event_id_of: dict[str, str] = {}

        # Stream payloads — keep only the small derived facts, never the heavy
        # JSONB, so this scales to the whole corpus in bounded memory.
        for payloads in store.iter_payloads(org, source, batch=batch):
            for p in payloads:
                cid = p.canonical_id
                canon_ids.add(cid)
                uf.find(cid)
                event_id_of[cid] = projector(p.payload, _mailbox_of(p.sightings), org).id
                for ref in reply_refs(p.payload):          # reply-chain edges
                    uf.union(cid, ref)
                for mbox, info in (p.sightings or {}).items():
                    tid = (info or {}).get("source_thread_id")
                    if tid:
                        threads[(mbox, tid)].append(cid)   # collect thread members

        for members in threads.values():                    # sighting edges
            for other in members[1:]:
                uf.union(members[0], other)

        # conversation_id = deterministic from the component's smallest REAL
        # message id (external ancestors don't get to name the conversation).
        root_min: dict[str, str] = {}
        for cid in canon_ids:
            r = uf.find(cid)
            if r not in root_min or cid < root_min[r]:
                root_min[r] = cid

        updates = []
        for cid in canon_ids:
            conv = make_id("gmail.conversation", f"{org}:{root_min[uf.find(cid)]}")
            updates.append({"id": event_id_of[cid], "conversation_id": conv})

        for c in range(0, len(updates), 2000):
            store.update_conversation_id(updates[c:c + 2000])

        # update_conversation_id's return value is just len(params) — an attempted
        # count, not a verified one (a batch of ids that match no real event row,
        # e.g. from a source-id mismatch, silently "succeeds" with 0 actual writes).
        # Recount for real so a bad id formula can never report a clean DONE again.
        updated = store.count_events_with_conversation_id(org, source)

        conversations = len({u["conversation_id"] for u in updates})
        # thread-fragments folded into cross-mailbox/time conversations:
        merged = len(threads) - conversations if len(threads) > conversations else 0
        result = {"payloads": len(canon_ids), "gmail_threads": len(threads),
                  "conversations": conversations, "events_labeled": updated,
                  "threads_merged": merged}
        log.info("[group] DONE %s", result)
        return result
    finally:
        store.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser(description="Group messages into conversations (step 1d)")
    ap.add_argument("--org", required=True)
    ap.add_argument("--source", default="gmail")
    args = ap.parse_args()
    setup_logging(org=args.org, source="group_conversations")
    group_conversations(args.org, source=args.source)


if __name__ == "__main__":
    main()
