"""
schema.py
─────────
Unified normalized format for all data ingested into Lore.
Both GitHub and Gmail data is converted into these common structures
so the knowledge brain (RAG / vector DB) sees one consistent schema.

Core idea:
  Everything is a KnowledgeEvent — something that happened.
  Every event has actors, context, content, and metadata.
  The knowledge brain queries KnowledgeEvents, not raw API responses.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime
import json


# ══════════════════════════════════════════════
# ACTOR — represents a person (unified across GitHub + Gmail)
# ══════════════════════════════════════════════
@dataclass
class Actor:
    id: str                          # Deterministic: "github:login" or "email:addr"
    display_name: str
    email: Optional[str] = None
    github_login: Optional[str] = None
    source: str = ""                 # "github" | "gmail"
    is_bot: bool = False
    is_deleted: bool = False

    def to_dict(self): return asdict(self)


# ══════════════════════════════════════════════
# KNOWLEDGE EVENT — the universal unit of knowledge
# ══════════════════════════════════════════════
@dataclass
class KnowledgeEvent:
    # Core identity
    id: str                         # SHA of (source + source_id) — dedup key
    source: str                     # "github" | "gmail"
    event_type: str                 # See EVENT TYPES below
    timestamp: str                  # ISO 8601

    # Content — what happened / what was said
    title: str                      # Short summary
    body: str                       # Full text content
    summary: str = ""               # AI-generated summary (filled later)

    # Who was involved
    actors: list[dict] = field(default_factory=list)   # list of Actor.to_dict()
    primary_actor: Optional[str] = None                 # Actor.id of main person

    # Links and references
    url: Optional[str] = None
    source_id: str = ""             # Original ID in source system (SHA, PR number, email ID)

    # Org context
    org: str = ""
    project: str = ""               # repo name or email thread subject
    tags: list[str] = field(default_factory=list)

    # Relations — what other events this relates to
    parent_id: Optional[str] = None       # e.g. PR that a commit belongs to
    related_ids: list[str] = field(default_factory=list)

    # Access control (ACL scopes)
    scope_id: Optional[str] = None
    scope_ids: list[str] = field(default_factory=list)
    extra_scope_ids: list[str] = field(default_factory=list)

    # Raw payload (kept for re-processing)
    raw: dict = field(default_factory=dict)

    def to_dict(self): return asdict(self)

    def to_json(self): return json.dumps(self.to_dict(), indent=2)

    def to_embedding_text(self) -> str:
        """
        Produces the text that gets embedded into the vector DB.
        Optimized for retrieval — includes all context in natural language.
        """
        lines = [
            f"Event type: {self.event_type}",
            f"Source: {self.source}",
            f"Time: {self.timestamp}",
            f"Project/context: {self.project}",
            f"Title: {self.title}",
        ]
        if self.primary_actor:
            lines.append(f"By: {self.primary_actor}")
        if self.actors:
            names = ", ".join(a.get("display_name", a.get("id", "")) for a in self.actors)
            lines.append(f"People involved: {names}")
        if self.body:
            lines.append(f"\n{self.body[:2000]}")   # cap at 2k chars for embeddings
        return "\n".join(lines)


# ══════════════════════════════════════════════
# EVENT TYPES — vocabulary for the knowledge brain
# ══════════════════════════════════════════════
class EventType:
    # GitHub
    GH_COMMIT        = "github.commit"
    GH_PR_OPENED     = "github.pr.opened"
    GH_PR_MERGED     = "github.pr.merged"
    GH_PR_CLOSED     = "github.pr.closed"
    GH_PR_REVIEW     = "github.pr.review"
    GH_PR_COMMENT    = "github.pr.comment"
    GH_CODE_CHANGE   = "github.code_change"

    # Gmail
    EMAIL_THREAD     = "gmail.thread"
    EMAIL_MESSAGE    = "gmail.message"
    TEAM_MESSAGE     = "ms_teams.message"
    EMAIL_DECISION   = "gmail.decision"     # AI-tagged: message that contains a decision

    # Zoho Cliq (Zoho Mail reuses EMAIL_MESSAGE — same shape as Gmail/Outlook)
    ZOHO_CLIQ_MESSAGE = "zoho_cliq.message"

    # Cross-source (enriched later)
    KNOWLEDGE_ITEM   = "lore.knowledge_item"
    # Slack
    SLACK_MESSAGE    = "slack.message.posted"
    SLACK_DELETED    = "slack.message.deleted"
    SLACK_REPLY      = "slack.thread.reply"
    SLACK_DISCUSSION = "slack.thread.discussion"
    SLACK_DM         = "slack.dm.message"
    SLACK_GDM        = "slack.gdm.message"


