"""
knowledge_distillation/models/ns3_2_nvidia.py
─────────────────────────────────────────────
Backward-compat shim. See
`knowledge_distillation/models/teachers.py:TEACHERS["ns3_2"]`.
"""

from knowledge_distillation.models.teachers import TEACHERS, TeacherConfig


def NS3_2Teacher() -> TeacherConfig:
    return TEACHERS["ns3_2"]
