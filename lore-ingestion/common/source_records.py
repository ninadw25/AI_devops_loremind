"""
common/source_records.py
────────────────────────
Normalized record types that every source plugin (GitLab, GitHub, …) emits.
The shared ingestion pipeline downstream is source-agnostic: it only consumes
these five models and never touches source-specific API shapes.

Contract:
  - A plugin MUST NOT write to storage directly.
  - A plugin MUST emit only these types across its boundary.
  - All ID composition (entity:, snap:, dev:) happens in the pipeline, not here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class RepoRef(BaseModel):
    """Stable pointer to a single repository on a single source."""

    source: Literal["gitlab", "github", "bitbucket", "gitea", "sourcehut"]
    repo_url: str
    """Canonical URL — e.g. https://gitlab.com/org/repo or https://github.com/org/repo.
    Used as the namespace component in entity IDs so the same repo on two hosts
    produces distinct nodes."""
    external_id: str
    """The source's own identifier. GitLab: numeric project ID. GitHub: owner/repo slug."""
    full_name: str
    """owner/repo form, same convention across sources."""
    default_branch: str = "main"


class NormalizedCommit(BaseModel):
    """One commit on one repo, normalized from any source."""

    repo: RepoRef
    sha: str
    short_id: str = ""
    title: str = ""
    message: str = ""
    author_name: str = ""
    author_email: str = ""
    authored_date: datetime
    committed_date: Optional[datetime] = None
    parent_shas: list[str] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
    web_url: str = ""

    @property
    def snapshot_id(self) -> str:
        return f"snap:{self.repo.repo_url}:{self.sha[:12]}"

    @property
    def classification(self) -> str:
        from chronograph.diffing.change_classifier import classify_commit
        return classify_commit(self.message)


class NormalizedFileChange(BaseModel):
    """One file in one commit's diff."""

    repo: RepoRef
    commit_sha: str
    path: str
    old_path: str = ""
    new_file: bool = False
    deleted_file: bool = False
    renamed_file: bool = False
    additions: int = 0
    deletions: int = 0


class NormalizedSourceFile(BaseModel):
    """Actual source text for one file at one ref."""

    repo: RepoRef
    ref: str
    path: str
    content: str
    language: Optional[str] = None


class NormalizedMember(BaseModel):
    """One human who can read the repo."""

    repo: RepoRef
    login: str
    name: str = ""
    email: str = ""
    role: Literal["owner", "maintainer", "developer", "reporter", "guest", "unknown"] = "unknown"
    avatar_url: str = ""
    web_url: str = ""
