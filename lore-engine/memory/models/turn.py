from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field

from memory.models.message import Message
from memory.models.context import RetrievedContext
from memory.models.tool import ToolCall


class Turn(BaseModel):
    turn_id: str    # sortable by time
    thread_id: str  # parent thread
    user_id: str    
    sequence: int
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    user_message: Message
    assistant_message: Message | None = None
    retrieved_context: list[RetrievedContext] = []
    tool_calls: list[ToolCall] = []
    token_usage: dict[str, int] = {}
    model: str = ""
    latency_ms: int | None = None
    status: Literal["pending", "completed", "failed"] = "pending"
    schema_version: int = 1
