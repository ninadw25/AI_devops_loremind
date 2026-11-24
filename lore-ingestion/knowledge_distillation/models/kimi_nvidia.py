"""
knowledge_distillation/models/kimi_nvidia.py
────────────────────────────────────────────
Backward-compat shim. See
`knowledge_distillation/models/teachers.py:TEACHERS["kimi"]`.
"""

from knowledge_distillation.models.teachers import TEACHERS, TeacherConfig


def KimiTeacher() -> TeacherConfig:
    return TEACHERS["kimi"]
