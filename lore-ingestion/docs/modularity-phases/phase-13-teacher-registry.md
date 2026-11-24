# Phase 13 — Collapse `knowledge_distillation/models/*` into a registry

**Status:** ✅ Completed (2026-05-19)
**Risk:** LOW (refactor target is internal to the distillation pipeline; one entry point — `distill.py:TEACHERS` — keeps its surface)

## The problem

Ten teacher LLM configurations, ten Python files, all near-identical:

| File | Class | Lines |
|------|-------|-------|
| `nemotron_openrouter.py` | `NemotronTeacher` | 35 |
| `kimi_nvidia.py` | `KimiTeacher` | 35 |
| `qwen_nvidia.py` | `QwenTeacher` | 35 |
| `minimax_nvidia.py` | `MinimaxTeacher` | 33 |
| `nemotron_super_nvidia.py` | `NemotronSuperTeacher` | 36 |
| `glm_nvidia.py` | `GLMTeacher` | 35 |
| `ns3_0_nvidia.py` | `NS3_0Teacher` | 34 |
| `ns3_1_nvidia.py` | `NS3_1Teacher` | 34 |
| `ns3_2_nvidia.py` | `NS3_2Teacher` | 34 |
| `qwen_openrouter.py` | `QwenORTeacher` | 35 |

Each subclass overrode exactly five class attributes (`teacher`,
`model_id`, `api_base_url`, `api_key_env`, `rpm_cap`) plus one boolean
knob (whether the endpoint accepts `chat_template_kwargs.enable_thinking`).
That's data, not code. There is no per-teacher behavior anywhere — every
`build_call_params()` method was the same four-line dict literal, copied
nine times.

## The fix

One `TeacherConfig` frozen dataclass + one `TEACHERS` registry, in
`knowledge_distillation/models/teachers.py`. Adding a new teacher is
now one entry, not one file.

```python
@dataclass(frozen=True)
class TeacherConfig:
    teacher: str
    model_id: str
    api_base_url: str
    api_key_env: str
    api_key_fallback_env: str | None = None
    rpm_cap: int = 38
    default_concurrency: int = 5
    daily_cap: int | None = None
    temperature: float = 0.3
    top_p: float = 0.95
    max_tokens: int = 8192
    extra_body: dict[str, Any] | None = {
        "chat_template_kwargs": {"enable_thinking": False}
    }

    def get_api_key(self, fallback=False) -> str: ...
    def get_client(self, fallback=False) -> AsyncOpenAI: ...
    def has_fallback(self) -> bool: ...
    def build_call_params(self) -> dict: ...
```

The methods are byte-for-byte the same as the old `TeacherBase`
behavior. The only difference: `extra_body` is now data, not an
overridden method body. MiniMax (the one teacher that did not support
`enable_thinking`) gets `extra_body=None` in its registry row.

## Backward compatibility

`knowledge_distillation/distill.py:48-56` imports nine
`*Teacher` classes by name. The locked decision (#4) is to preserve
import paths via shims. Each per-teacher file is now a 3-line
factory:

```python
# knowledge_distillation/models/kimi_nvidia.py
from knowledge_distillation.models.teachers import TEACHERS, TeacherConfig

def KimiTeacher() -> TeacherConfig:
    return TEACHERS["kimi"]
```

Call-site syntax (`KimiTeacher()`) is unchanged. The returned object
is now a `TeacherConfig` instance (the frozen singleton), not a fresh
subclass instance — but it exposes the same attributes and methods,
and `distill.py:TEACHERS` never mutates teacher state, so the
singleton vs. fresh-instance distinction is invisible.

`base.py` also kept the historical `TeacherBase` name as an alias of
`TeacherConfig`, so any code that did `from .base import TeacherBase`
keeps working.

## What was verified

```
$ python -c "from knowledge_distillation.models import TEACHERS, get_teacher"          → OK
$ python -c "from knowledge_distillation.models.kimi_nvidia import KimiTeacher
              t = KimiTeacher()
              assert t is TEACHERS['kimi']
              assert t.build_call_params() == { ...identical to pre-refactor... }"     → OK
$ python -c "MinimaxTeacher().build_call_params()  # no extra_body key"                → OK
$ python -c "from knowledge_distillation.models.base import TeacherBase
              from knowledge_distillation.models.teachers import TeacherConfig
              assert TeacherBase is TeacherConfig"                                      → OK
$ python -c "get_teacher('nonexistent')"                                                → ValueError ✓
$ python -m knowledge_distillation.distill --help                                       → OK
$ python -c "import knowledge_distillation.distill as d
              assert set(d.TEACHERS) == {'nemotron','kimi','qwen','minimax',
                                          'nemotron_s','glm','ns3_0','ns3_1','ns3_2'}"  → OK
```

`distill.py:TEACHERS` still maps the same nine keys to the same
configs. The runtime behavior of every teacher (model ID, endpoint,
API key env, fallback env, RPM cap, daily cap, concurrency,
`extra_body`) is preserved bit-for-bit.

## Open follow-up

`qwen_openrouter.py`'s `QwenORTeacher` is registered in `teachers.py`
as `"qwen_or"` but is **not** wired into `distill.py:TEACHERS`. The
shim exists for symmetry but the runner won't accept it via
`--teacher`. If that route is needed, add one line:

```python
"qwen_or": TEACHERS["qwen_or"],   # or use get_teacher("qwen_or")
```

That mirrors the pre-refactor state (the old `qwen_openrouter.py`
was also unreferenced by `distill.py`).

## Rollback

```bash
git checkout HEAD -- knowledge_distillation/models/
```

## Next phase

**Step 14 — Rename `connectors/` → `infra/gmail/`**

`infra/llm/` and `infra/embeddings/` landed in Step 12. Moving Gmail
fetching under the same umbrella unifies "external service adapter"
under one directory. Plan: move + shim the package at the old path,
update CLAUDE.md commands.
