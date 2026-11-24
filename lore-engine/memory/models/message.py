from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    metadata: dict[str, Any] = {}
