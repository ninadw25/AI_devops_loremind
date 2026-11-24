"""
knowledge_distillation.models
─────────────────────────────
Teacher LLM configurations for the distillation pipeline.

Canonical source: `teachers.py` (`TeacherConfig` dataclass + `TEACHERS`
registry + `get_teacher(name)`).

The per-teacher modules (`kimi_nvidia`, `qwen_nvidia`, …) are
backward-compat shims that re-expose the historical Camel-cased
constructors (`KimiTeacher()`, `QwenTeacher()`, …) used by
`knowledge_distillation/distill.py`.
"""

from knowledge_distillation.models.teachers import (
    TEACHERS,
    TeacherConfig,
    get_teacher,
)

# `TeacherBase` is an alias of `TeacherConfig` (see `base.py`) — kept
# importable for any code still doing `from .base import TeacherBase`.
from knowledge_distillation.models.base import TeacherBase

__all__ = ["TEACHERS", "TeacherConfig", "TeacherBase", "get_teacher"]
