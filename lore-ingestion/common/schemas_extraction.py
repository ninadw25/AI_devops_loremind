"""
common/schemas_extraction.py
──────────────────────────────
Pydantic schemas for triple extraction output validation.

Enforces:
  - Predicate must be from allowed enum
  - Node type must be from allowed enum
  - Evidence sentence must exist (min 5 chars)
  - Department tags from valid set
  - Sensitivity from valid set

Does NOT enforce (left to pipeline/validator.py):
  - Correct subject/object types per predicate (semantic)
  - Direction correctness (semantic)
  - Content quality (rubber stamps, garbage)
  - Email-as-name detection (content quality)
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from common.rbac import ALLOWED_DEPARTMENTS, ALLOWED_SENSITIVITY


class NodeType(str, Enum):
    PERSON = "Person"
    ORGANIZATION = "Organization"
    DECISION = "Decision"
    TASK = "Task"
    FACT = "Fact"
    TOPIC = "Topic"
    EVENT = "Event"
    # Jira structured nodes
    ISSUE = "Issue"
    SPRINT = "Sprint"
    COMPONENT = "Component"
    VERSION = "Version"


class Predicate(str, Enum):
    # Shared (email + Jira)
    DECIDED = "DECIDED"
    BECAUSE = "BECAUSE"
    ASSIGNED_TO = "ASSIGNED_TO"
    REGARDING = "REGARDING"
    SUBJECT_OF = "SUBJECT_OF"
    WORKS_AT = "WORKS_AT"
    PARTICIPATED_IN = "PARTICIPATED_IN"
    MENTIONS = "MENTIONS"
    STATED_BY = "STATED_BY"
    # Jira deterministic edges (from structured fields)
    REPORTED_BY = "REPORTED_BY"
    PART_OF_EPIC = "PART_OF_EPIC"
    SUBTASK_OF = "SUBTASK_OF"
    BLOCKS = "BLOCKS"
    BLOCKED_BY = "BLOCKED_BY"
    RELATES_TO = "RELATES_TO"
    DUPLICATES = "DUPLICATES"
    IN_SPRINT = "IN_SPRINT"
    HAS_COMPONENT = "HAS_COMPONENT"
    FIX_IN_VERSION = "FIX_IN_VERSION"
    LABELED = "LABELED"
    RESOLVED_BY = "RESOLVED_BY"
    # Jira provenance edge (added by merge step, not by LLM)
    DERIVED_FROM = "DERIVED_FROM"


class NodeRef(BaseModel):
    type: NodeType
    name: Optional[str] = None
    description: Optional[str] = None
    date: Optional[str] = None

    def label(self) -> str:
        return self.name or self.description or "unknown"


class Triple(BaseModel):
    subject: NodeRef
    predicate: Predicate
    object: NodeRef
    evidence_sentence: str = Field(min_length=5)
    # Provenance tag: "field" = deterministic from structured data, "llm" = from LLM prose pass.
    # None = untagged (legacy Gmail/Slack triples). Set programmatically, never by the LLM.
    derived: Optional[str] = None

    class Config:
        use_enum_values = True


class ExtractionResult(BaseModel):
    triples: list[Triple] = []
    thread_summary: str = ""
    department_tags: list[str] = Field(default_factory=lambda: ["general"])
    sensitivity: str = "internal"

    @field_validator("department_tags")
    @classmethod
    def validate_departments(cls, v):
        return [d for d in v if d in ALLOWED_DEPARTMENTS] or ["general"]

    @field_validator("sensitivity")
    @classmethod
    def validate_sensitivity(cls, v):
        return v if v in ALLOWED_SENSITIVITY else "internal"
