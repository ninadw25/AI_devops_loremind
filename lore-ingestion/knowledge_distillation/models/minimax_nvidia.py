"""
knowledge_distillation/models/minimax_nvidia.py
───────────────────────────────────────────────
Backward-compat shim. See
`knowledge_distillation/models/teachers.py:TEACHERS["minimax"]`.

(MiniMax-M2.7 does not support `chat_template_kwargs.enable_thinking`,
so `extra_body` is `None` in the registry row.)
"""

from knowledge_distillation.models.teachers import TEACHERS, TeacherConfig


def MinimaxTeacher() -> TeacherConfig:
    return TEACHERS["minimax"]
