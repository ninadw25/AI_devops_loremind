"""
lore_engine_grpc — gRPC server for the gateway → engine core query path.

Exposes QueryService (Query / QueryStream) and ThreadsService (ListThreads /
ListTurns / DeleteThread) over gRPC, as a typed, fail-closed replacement for
the prior HTTP/JSON + SSE hop. See docs/GRPC_TRANSPORT.md.

The generated protobuf Python bindings live under ``gen/`` and import each
other as the top-level package ``lore.engine.v1.*``. We put ``gen/`` on
``sys.path`` here (the package __init__) so those imports resolve everywhere
the package is imported from — parent-package __init__ always runs before any
submodule body, so submodules can freely ``import lore.engine.v1.query_pb2``.
"""
from __future__ import annotations

import os as _os
import sys as _sys

_GEN_DIR = _os.path.join(_os.path.dirname(__file__), "gen")
if _GEN_DIR not in _sys.path:
    _sys.path.insert(0, _GEN_DIR)

# Re-export the public surface so callers can do:
#   from lore_engine_grpc import build_server, serve_grpc
from lore_engine_grpc.server import build_server, serve_grpc  # noqa: E402,F401

__all__ = ["build_server", "serve_grpc"]
