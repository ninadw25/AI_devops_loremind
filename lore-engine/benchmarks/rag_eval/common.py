"""Shared helpers for the RAG evaluation (Neo4j + NVIDIA clients, settings from .env)."""
from __future__ import annotations

import os
from pathlib import Path

from neo4j import GraphDatabase
from openai import OpenAI

ENV = Path(__file__).resolve().parents[2] / ".env"
LLM_MODEL = "nvidia/nemotron-3-super-120b-a12b"   # automation LLM; see llm_model_choice
EMBED_MODEL = "nvidia/nemotron-3-embed-1b"


def env(key: str, default: str | None = None) -> str:
    """Process env first (CI), then the engine .env (local)."""
    if key in os.environ:
        return os.environ[key]
    for line in (ENV.read_text().splitlines() if ENV.exists() else []):
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    if default is not None:
        return default
    raise KeyError(key)


def neo4j_driver():
    return GraphDatabase.driver(env("NEO4J_URI", "bolt://localhost:7687"),
                                auth=(env("NEO4J_USERNAME", "neo4j"), env("NEO4J_PASSWORD")))


def nvidia() -> OpenAI:
    return OpenAI(api_key=env("NVIDIA_API_KEY"), base_url="https://integrate.api.nvidia.com/v1")
