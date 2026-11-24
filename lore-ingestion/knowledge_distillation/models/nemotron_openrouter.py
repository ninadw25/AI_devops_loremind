"""
knowledge_distillation/models/nemotron_openrouter.py
────────────────────────────────────────────────────
Backward-compat shim. The configuration moved to
`knowledge_distillation/models/teachers.py:TEACHERS["nemotron"]`.

This file preserves the historical `NemotronTeacher()` callable so
`knowledge_distillation/distill.py` keeps working without edits.
"""

from knowledge_distillation.models.teachers import TEACHERS, TeacherConfig


def NemotronTeacher() -> TeacherConfig:
    return TEACHERS["nemotron"]
