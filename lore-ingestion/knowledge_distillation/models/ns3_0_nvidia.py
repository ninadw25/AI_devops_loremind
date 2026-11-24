"""
knowledge_distillation/models/ns3_0_nvidia.py
─────────────────────────────────────────────
Backward-compat shim. See
`knowledge_distillation/models/teachers.py:TEACHERS["ns3_0"]`.
"""

from knowledge_distillation.models.teachers import TEACHERS, TeacherConfig


def NS3_0Teacher() -> TeacherConfig:
    return TEACHERS["ns3_0"]
