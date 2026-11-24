from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class RetrievedContext(BaseModel):
    chunk_id: str
    source: str          # node type: Decision, Fact, Task, Event, Person
    content: str
    score: float
    metadata: dict[str, Any] = {}
