"""
agent/run.py
────────────
CLI runner for the code-intelligence research agent.

Usage:
    # One-shot question
    python -m agent.run --ask "Who owns the authentication code in railgun?"

    # Interactive REPL
    python -m agent.run

    # Quiet mode (suppress tool traces, print final answer only)
    python -m agent.run --ask "What are the hotspots?" --quiet
"""

from __future__ import annotations

import argparse
import sys
import uuid
import logging

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.WARNING)   # suppress noisy HTTP logs by default
logging.getLogger("agent").setLevel(logging.INFO)

from agent.graph import build_agent, ask


BANNER = """
╔══════════════════════════════════════════════════════════╗
║       Lore Code-Intelligence Research Agent              ║
║  LangGraph ReAct · Nemotron-Super-49B · pgvector         ║
╚══════════════════════════════════════════════════════════╝
Tools: {tool_count} available
Type your question, or 'quit' / Ctrl-C to exit.
"""

EXAMPLE_QUESTIONS = [
    "List all ingested projects and their stats.",
    "Who are the top contributors to isitraghav/railgun by commit count?",
    "What are the most complex functions in isitraghav/railgun?",
    "Which files in railgun are a bus-factor risk (only one author)?",
    "Show me the commit timeline for railgun — how active is development?",
    "Find all code related to authentication or login in railgun.",
    "Trace the call graph for the most complex function in railgun.",
    "What commits mention 'fix' or 'bug' in railgun?",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Code-intelligence research agent")
    parser.add_argument("--ask", "-q", default="", help="One-shot question (skips REPL)")
    parser.add_argument("--quiet", action="store_true", help="Print final answer only")
    parser.add_argument("--model", default="meta/llama-3.1-70b-instruct",
                        help="NVIDIA model. Default: llama-3.1-70b (fast ~3s/turn). Deep: --model nvidia/llama-3.3-nemotron-super-49b-v1 (reasoning, slower)")
    parser.add_argument("--thread", default="", help="Resume a named conversation thread")
    args = parser.parse_args()

    print("Building agent...", end=" ", flush=True)
    from agent.tools import ALL_TOOLS
    agent = build_agent(model=args.model)
    print(f"ready ({len(ALL_TOOLS)} tools)\n")

    verbose = not args.quiet
    thread_id = args.thread or str(uuid.uuid4())

    if args.ask:
        ask(args.ask, agent=agent, thread_id=thread_id, verbose=verbose)
        return

    # ── Interactive REPL ──────────────────────────────────────────────
    print(BANNER.format(tool_count=len(ALL_TOOLS)))
    print(f"\033[2mThread: {thread_id}  (pass --thread {thread_id} to resume)\033[0m\n")
    print("Example questions:")
    for i, q in enumerate(EXAMPLE_QUESTIONS, 1):
        print(f"  {i}. {q}")
    print()

    while True:
        try:
            question = input("\n\033[1mYou:\033[0m ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit", "q"):
            print("Bye.")
            break

        # Allow typing a number to select an example question
        if question.isdigit() and 1 <= int(question) <= len(EXAMPLE_QUESTIONS):
            question = EXAMPLE_QUESTIONS[int(question) - 1]
            print(f"\033[2m→ {question}\033[0m")

        print()
        ask(question, agent=agent, thread_id=thread_id, verbose=verbose)
        print()


if __name__ == "__main__":
    main()
