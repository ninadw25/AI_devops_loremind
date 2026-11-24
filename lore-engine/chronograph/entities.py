"""
Chronograph domain entities — Pydantic v2 models for the temporal code knowledge graph.
"""

from __future__ import annotations
import hashlib
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

from core.hashing import short_hash


class ComplexityMetrics(BaseModel):
    """Code complexity measurements."""
    cyclomatic: int = 0
    cognitive: int = 0
    max_nesting: int = 0
    lines: int = 0
    params: int = 0


class Snapshot(BaseModel):
    """A point-in-time state of a repository (corresponds to a commit)."""
    model_config = {"extra": "allow"}

    id: str = ""
    repo_url: str
    commit_sha: str
    parent_sha: Optional[str] = None
    author_email: str = ""
    author_name: str = ""
    timestamp: datetime
    message: str = ""

    @model_validator(mode="after")
    def _set_id(self) -> Snapshot:
        if not self.id:
            self.id = f"snap:{self.repo_url}:{self.commit_sha[:12]}"
        return self


class CodeEntity(BaseModel):
    """A long-lived code symbol (function, class, module) tracked across commits."""
    model_config = {"extra": "allow"}

    id: str = ""
    kind: Literal["function", "class", "module", "method"]
    qualified_name: str
    file_path: str
    language: str
    repo_url: str = ""

    @model_validator(mode="after")
    def _set_id(self) -> CodeEntity:
        if not self.id:
            self.id = f"entity:{self.repo_url}:{self.file_path}:{self.qualified_name}"
        return self


class CodeEntityVersion(BaseModel):
    """A specific version of a CodeEntity at a particular commit."""
    model_config = {"extra": "allow"}

    id: str = ""
    entity_id: str
    snapshot_id: str
    content_hash: str
    start_line: int
    end_line: int
    signature: str = ""
    body_hash: str = ""
    complexity: Optional[ComplexityMetrics] = None

    @model_validator(mode="after")
    def _set_id(self) -> CodeEntityVersion:
        if not self.id:
            self.id = f"ver:{self.entity_id}:{self.snapshot_id}"
        return self


class TemporalEdge(BaseModel):
    """A relationship between two entities with temporal validity."""
    model_config = {"extra": "allow"}

    id: str = ""
    source_id: str
    target_id: str
    rel_type: Literal["CONTAINS", "CALLS", "IMPORTS", "TESTED_BY", "AUTHORED_BY"]
    valid_from: datetime
    valid_to: Optional[datetime] = None
    properties: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _set_id(self) -> TemporalEdge:
        if not self.id:
            raw = f"{self.source_id}:{self.target_id}:{self.rel_type}:{self.valid_from.isoformat()}"
            self.id = f"edge:{short_hash(raw, length=16)}"
        return self


class DiffRecord(BaseModel):
    """Records a change to a CodeEntity between two snapshots."""
    model_config = {"extra": "allow"}

    id: str = ""
    entity_id: str
    from_version_id: Optional[str] = None
    to_version_id: Optional[str] = None
    change_type: Literal["added", "removed", "modified", "renamed", "moved",
                          "signature_change", "body_change", "docstring_change"]
    snapshot_id: str
    details: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _set_id(self) -> DiffRecord:
        if not self.id:
            raw = f"{self.entity_id}:{self.snapshot_id}:{self.change_type}"
            self.id = f"diff:{short_hash(raw, length=16)}"
        return self


class Symbol(BaseModel):
    """Extracted symbol from AST parsing."""
    name: str
    kind: Literal["function", "class", "module", "method"]
    qualified_name: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    start_byte: int = 0
    end_byte: int = 0
    signature: str = ""
    body_text: str = ""
    body_hash: str = ""
    decorators: list[str] = Field(default_factory=list)
    params: list[str] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    complexity: Optional[ComplexityMetrics] = None

    @model_validator(mode="after")
    def _compute_body_hash(self) -> Symbol:
        if self.body_text and not self.body_hash:
            self.body_hash = hashlib.sha256(self.body_text.encode()).hexdigest()
        return self


class FileChange(BaseModel):
    """A single file change within a commit."""
    path: str
    old_path: Optional[str] = None
    status: Literal["added", "modified", "deleted", "renamed"]
    old_blob_id: Optional[str] = None
    new_blob_id: Optional[str] = None
    old_content: Optional[bytes] = None
    new_content: Optional[bytes] = None

    model_config = {"arbitrary_types_allowed": True}


class CommitData(BaseModel):
    """Raw commit data from git walker."""
    sha: str
    parent_sha: Optional[str] = None
    author_email: str = ""
    author_name: str = ""
    timestamp: datetime
    message: str = ""
    files_changed: list[FileChange] = Field(default_factory=list)
