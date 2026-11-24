from __future__ import annotations
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field


class Thread(BaseModel):
    thread_id: str
    user_id: str
    title: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    turn_count: int = 0
    total_tokens: int = 0
    summary: str | None = None
    summary_up_to_sequence: int | None = None
    status: Literal["active", "archived", "deleted"] = "active"
    schema_version: int = 1
