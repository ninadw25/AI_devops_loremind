"""
knowledge_distillation/models/qwen_nvidia.py
────────────────────────────────────────────
Backward-compat shim. See
`knowledge_distillation/models/teachers.py:TEACHERS["qwen"]`.
"""

from knowledge_distillation.models.teachers import TEACHERS, TeacherConfig


def QwenTeacher() -> TeacherConfig:
    return TEACHERS["qwen"]
