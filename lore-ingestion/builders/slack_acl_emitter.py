"""
builders/slack_acl_emitter.py
──────────────────────────────
Transforms the raw discovery artifact (output/slack/{org}_scope_principals.json)
into a backend-ready user-scopes lookup (output/slack/{org}_user_scopes.json).

Why this exists
---------------
The discovery artifact is optimised for ingestion (normalised: scopes / principals /
scope_principals as three separate lists). The backend needs the inverse: given a
logged-in user, what scope_ids may they read? This emitter does that inversion once
at build time so the backend reads a flat file, not a join.

Public-channel rule
-------------------
Public channels in Slack have NO explicit membership rows in scope_principals — any
workspace member can read them. The emitter expands this: every principal in the
artifact gets every public channel scope_id added to their scope list automatically.

Output shape  (output/slack/{org}_user_scopes.json)
----------------------------------------------------
{
  "org": "techtest",
  "generated_at": "2026-06-07T12:00:00+00:00",
  "users": [
    {
      "principal_id": "slack:U789ABC",
      "display_name": "Ravi Kumar",
      "email": "ravi@ezobooks.in",
      "scope_ids": [
        "slack:workspace:T001",
        "slack:channel:C_PUBLIC_1",
        "slack:channel:C_PUBLIC_2",
        "slack:channel:C_PRIVATE_MEMBER"
      ]
    }
  ],
  "channel_directory": [
    { "scope_id": "slack:channel:C_PUBLIC_1", "name": "#general", "is_public": true  },
    { "scope_id": "slack:channel:C_PRIVATE_MEMBER", "name": "#devops", "is_public": false }
  ]
}

Run
---
    ../.venv/bin/python -m builders.slack_acl_emitter --org techtest
    ../.venv/bin/python -m builders.slack_acl_emitter --org ezobooks --data-dir output
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from common.logger import get_logger, setup_logging
from common.output_paths import SlackPaths
from common.stage_summary import log_summary

log = get_logger("builders.slack_acl_emitter")


def emit_user_scopes(org: str, data_dir: Path) -> dict:
    """Read scope_principals.json → write user_scopes.json.

    Returns the emitted artifact as a dict.
    """
    paths = SlackPaths(org, data_dir)
    src = paths.scope_principals
    if not src.exists():
        raise FileNotFoundError(
            f"scope_principals.json not found at {src}. "
            "Run discovery first: python -m infra.slack.run --org {org} --stage discovery"
        )

    raw = json.loads(src.read_text(encoding="utf-8"))
    scopes: list[dict] = raw.get("scopes", [])
    principals: list[dict] = raw.get("principals", [])
    scope_principals: list[dict] = raw.get("scope_principals", [])

    # ── Classify scopes ───────────────────────────────────────────────
    workspace_scope_ids: list[str] = []
    public_channel_scope_ids: list[str] = []
    channel_directory: list[dict] = []

    for s in scopes:
        sid = s["scope_id"]
        kind = s.get("kind", "")
        if kind == "workspace":
            workspace_scope_ids.append(sid)
            continue
        is_public = s.get("is_public", False)
        channel_directory.append({
            "scope_id": sid,
            "name": s.get("name", ""),
            "is_public": is_public,
        })
        if is_public:
            public_channel_scope_ids.append(sid)

    # ── Build principal → private scope_ids map ───────────────────────
    private_by_principal: dict[str, list[str]] = {}
    for sp in scope_principals:
        pid = sp["principal_id"]
        sid = sp["scope_id"]
        private_by_principal.setdefault(pid, []).append(sid)

    # ── Base scope_ids every workspace member gets ────────────────────
    # workspace scope + every public channel (workspace-wide visibility)
    base_scope_ids: list[str] = sorted(
        set(workspace_scope_ids) | set(public_channel_scope_ids)
    )

    # ── Expand each principal into their full effective scope_ids ─────
    users: list[dict] = []
    for p in principals:
        pid = p["principal_id"]
        effective = sorted(
            set(base_scope_ids) | set(private_by_principal.get(pid, []))
        )
        users.append({
            "principal_id": pid,
            "display_name": p.get("display_name") or "",
            "email": p.get("email") or None,
            "scope_ids": effective,
        })

    users.sort(key=lambda u: u["principal_id"])
    channel_directory.sort(key=lambda c: c["scope_id"])

    artifact = {
        "org": org,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "users": users,
        "channel_directory": channel_directory,
    }

    out = paths.user_scopes
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")

    log.info(
        "Emitted %d users, %d channels (%d public, %d private) → %s",
        len(users),
        len(channel_directory),
        len(public_channel_scope_ids),
        len(channel_directory) - len(public_channel_scope_ids),
        out,
    )
    log_summary(
        "slack_acl_emitter",
        org=org,
        users=len(users),
        channels=len(channel_directory),
        public=len(public_channel_scope_ids),
        private=len(channel_directory) - len(public_channel_scope_ids),
    )
    return artifact


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Emit backend-ready user→scope_ids lookup from Slack discovery artifact"
    )
    ap.add_argument("--org", required=True)
    ap.add_argument("--data-dir", default="output", type=Path)
    args = ap.parse_args(argv)

    logs_dir = args.data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(org=args.org, log_dir=str(logs_dir), source="slack_acl_emitter")

    emit_user_scopes(args.org, args.data_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
