"""scripts/zoho_dump.py
Fetch all Zoho Mail messages (with full body) and Cliq messages,
saving raw JSON to a local folder.

Usage:
    python scripts/zoho_dump.py --out zoho_data/
    python scripts/zoho_dump.py --out zoho_data/ --limit 50   # cap per-source
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE = "https://mail.zoho.in/api"
CLIQ_BASE = "https://cliq.zoho.in/api/v2"
HTML_RE = re.compile("<[^>]+>")


# ── Auth ──────────────────────────────────────────────────────────────

def get_token() -> str:
    from infra.zoho.fetcher import get_access_token
    return get_access_token()


def headers(tok: str) -> dict:
    return {"Authorization": f"Zoho-oauthtoken {tok}", "Accept": "application/json"}


# ── HTTP helper ───────────────────────────────────────────────────────

def get(url: str, tok: str, params: dict | None = None) -> dict:
    for attempt in range(5):
        r = httpx.get(url, headers=headers(tok), params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"GET {url} failed after retries")


# ── Mail ──────────────────────────────────────────────────────────────

def fetch_mail_accounts(tok: str) -> list[dict]:
    return get(f"{BASE}/accounts", tok).get("data", [])


def fetch_all_mail(tok: str, account_id: str, limit: int | None) -> list[dict]:
    messages, start, page = [], 1, 200
    while True:
        rows = get(f"{BASE}/accounts/{account_id}/messages/view", tok,
                   {"start": start, "limit": page}).get("data", [])
        if not rows:
            break
        for row in rows:
            fid = row.get("folderId")
            mid = row.get("messageId")
            # fetch full content using correct endpoint: /folders/{fid}/messages/{mid}/content
            try:
                resp = get(f"{BASE}/accounts/{account_id}/folders/{fid}/messages/{mid}/content", tok)
                raw_html = (resp.get("data") or {}).get("content", "")
                row["body_html"] = raw_html
                row["body_text"] = HTML_RE.sub("", raw_html).strip()
            except Exception as e:
                row["body_html"] = ""
                row["body_text"] = f"[content fetch failed: {e}]"
            messages.append(row)
            if limit and len(messages) >= limit:
                return messages
        if len(rows) < page:
            break
        start += page
    return messages


# ── Cliq ─────────────────────────────────────────────────────────────

def fetch_all_cliq(tok: str, limit: int | None) -> dict[str, list[dict]]:
    """Returns {channel_name: [messages]}. Empty dict if no Cliq org."""
    try:
        channels_resp = get(f"{CLIQ_BASE}/channels", tok)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            print("  Cliq: no org account — skipping")
            return {}
        raise
    channels = channels_resp.get("channels", [])

    result: dict[str, list[dict]] = {}
    for ch in channels:
        cid = str(ch.get("chat_id") or ch.get("id") or "")
        name = ch.get("name") or ch.get("title") or cid
        if not cid:
            continue
        msgs, next_tok = [], None
        while True:
            params: dict = {"limit": 100}
            if next_tok:
                params["next_token"] = next_tok
            page = get(f"{CLIQ_BASE}/channels/{cid}/messages", tok, params)
            rows = page.get("messages", [])
            for r in rows:
                msgs.append({**r, "channel_id": cid, "channel_name": name})
                if limit and len(msgs) >= limit:
                    break
            if limit and len(msgs) >= limit:
                break
            next_tok = page.get("next_token")
            if not next_tok or not rows:
                break
        result[name] = msgs
        print(f"  Cliq channel '{name}': {len(msgs)} messages")
    return result


# ── Save ─────────────────────────────────────────────────────────────

def save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved → {path}  ({path.stat().st_size // 1024} KB)")


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="zoho_data", help="Output folder")
    ap.add_argument("--limit", type=int, default=None, help="Max messages per source")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tok = get_token()
    print(f"Token OK. Saving to {out}/\n")

    # ── Mail ──────────────────────────────────────────────────────────
    print("=== Zoho Mail ===")
    accounts = fetch_mail_accounts(tok)
    print(f"Accounts: {len(accounts)}")
    all_mail: list[dict] = []
    for acc in accounts:
        aid = acc.get("accountId")
        email = acc.get("primaryEmailAddress") or acc.get("mailboxAddress") or aid
        print(f"  Fetching {email}...")
        msgs = fetch_all_mail(tok, aid, args.limit)
        print(f"    {len(msgs)} messages")
        for m in msgs:
            m["_account_email"] = email
        all_mail.extend(msgs)

    save(out / f"mail_{ts}.json", all_mail)
    print(f"Total mail messages: {len(all_mail)}\n")

    # ── Cliq ──────────────────────────────────────────────────────────
    print("=== Zoho Cliq ===")
    cliq = fetch_all_cliq(tok, args.limit)
    if cliq:
        save(out / f"cliq_{ts}.json", cliq)
        total = sum(len(v) for v in cliq.values())
        print(f"Total Cliq messages: {total}\n")
    else:
        print("No Cliq data saved.\n")

    # ── Index ─────────────────────────────────────────────────────────
    index = {
        "generated_at": ts,
        "mail_messages": len(all_mail),
        "cliq_channels": len(cliq),
        "cliq_messages": sum(len(v) for v in cliq.values()),
        "files": [str(p.relative_to(out)) for p in sorted(out.glob("*.json")) if p.name != "index.json"],
    }
    save(out / "index.json", index)
    print("Done.")


if __name__ == "__main__":
    main()
