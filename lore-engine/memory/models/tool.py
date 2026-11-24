from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class ToolCall(BaseModel):
    """Stub for Phase 4 agentic tool tracking."""
    tool_call_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = {}
    result: Any = None
    error: str | None = None
