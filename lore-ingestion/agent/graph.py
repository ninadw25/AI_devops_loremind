"""
agent/graph.py
──────────────
LangGraph ReAct agent for deep code-intelligence research.

Architecture:
  LLM (NVIDIA meta/llama-3.3-70b-instruct, tool-calling capable)
    └── ReAct loop (langgraph.prebuilt.create_react_agent)
          ├── list_ingested_projects
          ├── get_project_stats
          ├── get_contributors
          ├── get_bus_factor
          ├── get_developer_ownership
          ├── get_collaboration_pairs
          ├── get_commit_timeline
          ├── search_commits
          ├── get_hotspot_files
          ├── get_recent_commits
          ├── search_symbols
          ├── get_function_source
          ├── get_file_content
          ├── get_complexity_report
          ├── trace_function_calls
          ├── get_call_graph_edges
          └── semantic_code_search

The agent plans, calls tools, inspects results, and iterates until it has
enough evidence to synthesise a complete answer. It stops when the LLM emits
a final message with no further tool calls.
"""

from __future__ import annotations

import os
import logging
import uuid
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

from agent.tools import ALL_TOOLS
from common.config import get_settings

log = logging.getLogger("agent.graph")

_SYSTEM_PROMPT = """You are a senior engineering intelligence analyst. You do NOT report data —
you interpret it. Think like a staff engineer reviewing a colleague, not a search engine returning results.

TOOL SELECTION:
- Project unclear → list_ingested_projects first
- "Who did / implemented / added X" → search_commits(query=X), read author_name from result
- Person / persona / contribution → build_developer_persona (reads commits, PRs, issues, code)
- "Last N days/weeks/month" time-bounded person question → call get_developer_commit_timeline(bucket='week') first, filter to the requested window, THEN build_developer_persona for full context. Explicitly state which commits fall in the window.
- File not found → fetch_file_from_github (for .md/.yaml/.json not in code index)
- Empty result → rephrase and retry once

INTERPRETATION RULES — apply these when reading tool output:

FILE PATTERNS reveal ENGINEERING ROLE (infer and state this explicitly):
  .github/workflows/, Dockerfile, *.yml  → DevOps / infrastructure / CI-CD owner
  parser/, decoder/, lexer/, spec        → protocol / language implementer
  test/, spec/, __tests__/               → quality / reliability engineer
  docs/, vitepress, README               → developer experience / documentation
  auth/, session/, token/                → security domain owner
  migrations/, schema/, db/             → data layer engineer
  benchmark/, perf/                      → performance engineer

COMMIT MESSAGE PATTERNS reveal WORKING STYLE:
  Mostly "fix:" → reactive, maintenance-driven contributor
  Mostly "feat:" → feature builder, product-focused
  Mostly "docs:" / "chore:" → maintainer, infrastructure steward
  Mixed → generalist / senior contributor

OWNERSHIP DEPTH interpretation:
  100% sole author → single point of failure AND deepest expert
  60-99% → domain lead with some collaboration
  20-59% → significant contributor, not the owner
  <20% → broad contributor, reviewer-level familiarity

ALWAYS do this for persona questions:
1. Infer their ENGINEERING ROLE from file patterns (CI engineer, protocol implementer, etc.)
2. Describe what they ACTUALLY BUILT by reading commit messages and PR descriptions
3. Assess their INFLUENCE in the team from collaboration network
4. State the RISK clearly: what breaks if they leave and why
5. Give a SHORT VERDICT: one sentence summary a hiring manager would read

NEVER just list bullet points of file names and percentages. Connect the dots.
Example bad: "They own .github/workflows/pr-title.yml (100%)"
Example good: "They are the sole infrastructure engineer — responsible for the entire CI/CD
pipeline including PR linting, automated releases, and deployment workflows. The project's
automation layer would be orphaned if they left."

Only reason from tool output. Never fabricate."""


def build_agent(model: str | None = None) -> Any:
    """Build and return the LangGraph ReAct agent with MemorySaver checkpointing.

    Provider is OpenAI-compatible. Resolution order for endpoint + key:
      1. LLM_BASE_URL + LLM_API_KEY env vars (any OpenAI-compatible proxy
         e.g. Bedrock-Mantle, vLLM, etc.) — model from LLM_MODEL or `model` arg
      2. NVIDIA_API_KEY (default — uses NVIDIA Build endpoint)
    """
    settings = get_settings()

    llm_base = os.environ.get("LLM_BASE_URL", "").strip()
    llm_key  = os.environ.get("LLM_API_KEY", "").strip()
    llm_mdl  = os.environ.get("LLM_MODEL", "").strip() or model

    if llm_base and llm_key:
        if not llm_mdl:
            raise RuntimeError("LLM_BASE_URL set but LLM_MODEL not specified")
        api_base = llm_base
        api_key  = llm_key
        chosen_model = llm_mdl
        is_nemotron = False  # 3rd-party proxies don't take NVIDIA's thinking config
    else:
        api_key = settings.nvidia_api_key
        if not api_key:
            raise RuntimeError("Set LLM_BASE_URL+LLM_API_KEY+LLM_MODEL, or NVIDIA_API_KEY")
        api_base = "https://integrate.api.nvidia.com/v1"
        chosen_model = model or "meta/llama-3.1-70b-instruct"
        is_nemotron = "nemotron" in chosen_model.lower()

    os.environ.setdefault("OPENAI_API_KEY", api_key)

    # Thinking config for Nemotron-super — only on NVIDIA endpoint
    extra_body = (
        {"chat_template_kwargs": {"thinking": {"type": "enabled", "budget_tokens": 200}}}
        if is_nemotron else {}
    )

    llm = ChatOpenAI(
        model=chosen_model,
        openai_api_key=api_key,
        openai_api_base=api_base,
        temperature=0.3,
        max_tokens=2048,   # enough for answer; tool-selection turns use <100 tokens
        max_retries=6,
        model_kwargs={"parallel_tool_calls": False},
        extra_body=extra_body if extra_body else None,
    )

    # MemorySaver keeps full message history in-process per thread_id.
    # Each conversation thread is isolated — passing the same thread_id
    # on subsequent ask() calls resumes the conversation with full context.
    checkpointer = MemorySaver()

    agent = create_react_agent(
        model=llm,
        tools=ALL_TOOLS,
        prompt=SystemMessage(content=_SYSTEM_PROMPT),
        checkpointer=checkpointer,
    )
    log.info("[agent] Built ReAct agent — model=%s tools=%d (memory=on)", model, len(ALL_TOOLS))
    return agent


def ask(
    question: str,
    agent=None,
    thread_id: Optional[str] = None,
    verbose: bool = True,
) -> str:
    """
    Send a question to the agent and return the final answer.
    Pass the same thread_id across calls to maintain conversation context.

    Args:
        question:  Natural language research question
        agent:     Pre-built agent (builds one on first call if None)
        thread_id: Conversation thread ID. Same ID = full history visible to LLM.
        verbose:   Print each tool call and result as it streams
    """
    if agent is None:
        agent = build_agent()

    if thread_id is None:
        thread_id = "default"

    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 40,   # allow up to 40 tool-call iterations for deep research
    }
    log.info("[agent] thread=%s question=%s", thread_id, question[:80])

    events = agent.stream(
        {"messages": [HumanMessage(content=question)]},
        config=config,
        stream_mode="values",
    )

    final_message = ""
    for event in events:
        msg = event["messages"][-1]
        if verbose:
            _print_event(msg)
        final_message = msg.content if hasattr(msg, "content") else str(msg)

    return final_message


def _truncate_tool_result(content: str, max_chars: int = 4000) -> str:
    """Truncate large tool results so they don't blow up LLM context."""
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + f"\n... [truncated — {len(content)-max_chars} chars omitted]"


def _print_event(msg) -> None:
    """Pretty-print a LangGraph message event to stdout."""
    kind = type(msg).__name__
    if kind == "AIMessage":
        # Show thinking/reasoning if present (Nemotron reasoning model)
        content = msg.content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "thinking":
                        thinking_text = block.get("thinking", "")
                        if thinking_text:
                            print(f"\n\033[35m[thinking]\033[0m {thinking_text[:800]}{'...' if len(thinking_text)>800 else ''}")
                    elif block.get("type") == "text" and block.get("text"):
                        if not msg.tool_calls:
                            print(f"\n\033[92m[answer]\033[0m {block['text']}")
            content = " ".join(b.get("text","") for b in content if isinstance(b,dict) and b.get("type")=="text")

        if msg.tool_calls:
            for tc in msg.tool_calls:
                print(f"\n\033[94m[tool call]\033[0m {tc['name']}({_fmt_args(tc['args'])})")
        elif content and isinstance(content, str):
            print(f"\n\033[92m[answer]\033[0m {content}")
    elif kind == "ToolMessage":
        preview = str(msg.content)[:400].replace("\n", " ")
        print(f"\033[90m  → {preview}{'...' if len(str(msg.content)) > 400 else ''}\033[0m")


def _fmt_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 40:
            v_str = v_str[:40] + "…"
        parts.append(f"{k}={v_str!r}")
    return ", ".join(parts)
