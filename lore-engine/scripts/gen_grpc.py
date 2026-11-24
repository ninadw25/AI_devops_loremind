#!/usr/bin/env python
"""
Regenerate the Python gRPC bindings from the .proto files.

Run from the lore-engine repo root:

    uv run --extra grpc-codegen python scripts/gen_grpc.py

(or:  python -m grpc_tools.protoc ...  with grpcio-tools installed)

The generated *_pb2.py / *_pb2_grpc.py / *_pb2.pyi files are committed to the
repo under lore_engine_grpc/gen/ (no protoc needed to build or run). Re-run
this script whenever a .proto changes, then review + commit the diff.

The gateway (elysia-backend) consumes the SAME protos and commits its own
generated TypeScript; see that repo's apps/core-backend/src/ai-server/grpc/README
for how to regenerate the TS side from these .proto files.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTO_DIR = ROOT / "proto"
GEN_DIR = ROOT / "lore_engine_grpc" / "gen"

# The committed gencode must declare protobuf 6.x (not 7.x) so it still loads
# under the protobuf<7.0 cap that observability pulls in transitively
# (langfuse→opentelemetry-proto). Only grpcio-tools 1.80.x emits 6.x gencode;
# 1.81+ emits 7.x and re-introduces the LOR-111 blocker. This guard makes a
# careless `pip install grpcio-tools` fail loudly instead of silently shipping
# unloadable gencode. See pyproject.toml [grpc-codegen] for the matching pin.
_REQUIRED_GRPCIO_TOOLS_MAJOR_MINOR = (1, 80)


def _check_toolchain() -> None:
    """Abort early if grpcio-tools isn't the pinned 1.80.x line."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        version_str = version("grpcio-tools")
    except PackageNotFoundError as exc:
        raise SystemExit(
            "grpcio-tools is not installed in this interpreter. Run via "
            "`uv run --extra grpc-codegen python scripts/gen_grpc.py`."
        ) from exc
    parts = tuple(int(p) for p in version_str.split(".")[:2] if p.isdigit())
    if parts[:2] != _REQUIRED_GRPCIO_TOOLS_MAJOR_MINOR:
        raise SystemExit(
            f"grpcio-tools {version_str} is not supported for codegen — gencode "
            "would declare protobuf 7.x and fail to load under the "
            "protobuf<7.0 observability cap. Reinstall the pinned line: "
            "grpcio-tools>=1.80.0,<1.81.0"
        )


def main() -> int:
    _check_toolchain()
    protos = sorted(PROTO_DIR.glob("lore/engine/v1/*.proto"))
    if not protos:
        print("no .proto files found under proto/lore/engine/v1/", file=sys.stderr)
        return 1

    GEN_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "grpc_tools.protoc",
        f"-I{PROTO_DIR}",
        f"--python_out={GEN_DIR}",
        f"--grpc_python_out={GEN_DIR}",
        f"--pyi_out={GEN_DIR}",
        *(str(p) for p in protos),
    ]
    print(">", " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
