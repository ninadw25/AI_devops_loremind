"""
knowledge_distillation/models/ns3_1_nvidia.py
─────────────────────────────────────────────
Backward-compat shim. See
`knowledge_distillation/models/teachers.py:TEACHERS["ns3_1"]`.
"""

from knowledge_distillation.models.teachers import TEACHERS, TeacherConfig


def NS3_1Teacher() -> TeacherConfig:
    return TEACHERS["ns3_1"]
