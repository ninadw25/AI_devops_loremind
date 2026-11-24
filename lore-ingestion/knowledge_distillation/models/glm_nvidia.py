"""
knowledge_distillation/models/glm_nvidia.py
───────────────────────────────────────────
Backward-compat shim. See
`knowledge_distillation/models/teachers.py:TEACHERS["glm"]`.
"""

from knowledge_distillation.models.teachers import TEACHERS, TeacherConfig


def GLMTeacher() -> TeacherConfig:
    return TEACHERS["glm"]
