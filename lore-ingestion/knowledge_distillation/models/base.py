"""
knowledge_distillation/models/base.py
─────────────────────────────────────
Backward-compatibility shim.

The teacher configuration model used to be an ABC (`TeacherBase`) with
one subclass per teacher. As of the modularity migration it is a
frozen dataclass (`TeacherConfig`) defined in
`knowledge_distillation/models/teachers.py`, with the per-teacher
factories living in their original files as one-line wrappers.

This module keeps `TeacherBase` importable as an alias of
`TeacherConfig` so any external code that did
`from knowledge_distillation.models.base import TeacherBase` keeps
working unchanged.
"""

from knowledge_distillation.models.teachers import TeacherConfig

# `TeacherBase` and `TeacherConfig` are now the same class.
TeacherBase = TeacherConfig

__all__ = ["TeacherBase", "TeacherConfig"]
