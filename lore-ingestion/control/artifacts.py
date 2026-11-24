"""
control/artifacts.py
────────────────────
Publish a run's identity + scope_principals artifacts to per-org S3 keys so
elysia-rbac (ARTIFACT_SOURCE=s3) can load them. Key layout matches the rbac
S3DirSource: `{org}/identity/{org}_identity.json` and
`{org}/{source}/{org}_scope_principals.json`.

No-op when S3_ARTIFACT_BUCKET is unset (local/EFS handoff) or boto3 is absent.
Bucket-level SSE-KMS handles encryption at rest.
"""

from __future__ import annotations

from pathlib import Path

from common.config import get_settings
from common.logger import get_logger

log = get_logger("control.artifacts")

# Scope-principal artifacts published per source (output subdir == S3 subdir).
_SCOPE_SOURCES = ["slack", "jira", "outlook"]


def upload_org_artifacts(org: str, data_dir: str) -> int:
    """Upload identity + scope artifacts for `org`. Returns objects uploaded."""
    s = get_settings()
    if not s.s3_artifact_bucket:
        return 0
    try:
        import boto3  # lazy — only needed when S3 is configured
    except ImportError:
        log.warning("boto3 not installed — skipping S3 artifact upload")
        return 0

    base = Path(data_dir)
    client = boto3.client("s3", region_name=s.s3_region)
    uploaded = 0

    def _put(local: Path, key: str) -> None:
        nonlocal uploaded
        if local.exists():
            client.upload_file(str(local), s.s3_artifact_bucket, key)
            uploaded += 1
            log.info("artifact_uploaded key=%s", key)

    _put(
        base / "identity" / f"{org}_identity.json",
        f"{org}/identity/{org}_identity.json",
    )
    for src in _SCOPE_SOURCES:
        _put(
            base / src / f"{org}_scope_principals.json",
            f"{org}/{src}/{org}_scope_principals.json",
        )
    return uploaded
