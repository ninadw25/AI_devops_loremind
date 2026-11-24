"""
knowledge_distillation/models/nemotron_super_nvidia.py
──────────────────────────────────────────────────────
Backward-compat shim. See
`knowledge_distillation/models/teachers.py:TEACHERS["nemotron_s"]`.
"""

from knowledge_distillation.models.teachers import TEACHERS, TeacherConfig


def NemotronSuperTeacher() -> TeacherConfig:
    return TEACHERS["nemotron_s"]
