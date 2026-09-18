"""Local CLI chat — no AWS deploy needed to try the Knowledge Agent.

Usage:
    python -m jira_agents.cli --agent knowledge
    python -m jira_agents.cli --agent knowledge --source snow --message "VPN outage fix?"
    python -m jira_agents.cli --agent triage --message "PROD outage: checkout 500s"
"""
from __future__ import annotations

import argparse

from .agents.base import AgentRequest
from .agents.registry import get_agent, list_agents


def _build_request(args) -> AgentRequest:
    return AgentRequest(
        message=args.message or "",
        project_filter=args.project,
        group_filter=args.group,
        source_filter=args.source,
        app_code=args.app,
        top_k=args.top_k,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Chat with a KB agent (JIRA + ServiceNow, local)")
    ap.add_argument("--agent", default="knowledge", help=f"one of {list_agents()}")
    ap.add_argument("--message", default=None)
    ap.add_argument("--project", default=None, help="JIRA project filter (e.g. PROJ)")
    ap.add_argument("--group", default=None, help="ServiceNow assignment-group filter")
    ap.add_argument("--source", default=None, choices=["jira", "snow"],
                    help="KB source (default: both)")
    ap.add_argument("--app", default=None, help="Application code (applications table)")
    ap.add_argument("--top-k", type=int, default=None)
    args = ap.parse_args()

    agent = get_agent(args.agent)
    if args.message:
        resp = agent.invoke(_build_request(args))
        print(resp.answer)
        if resp.citations:
            print("\nSources:")
            for c in resp.citations[:5]:
                print(f"  [{c.jira_key}] ({c.source}) {c.summary} ({c.jira_url})")
        return

    print(f"Chatting with '{args.agent}'. Type 'exit' to quit.")
    while True:
        try:
            msg = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if msg.lower() in ("exit", "quit"):
            break
        if not msg:
            continue
        args.message = msg
        resp = agent.invoke(_build_request(args))
        print(f"\n{resp.agent}> {resp.answer}")
        if resp.citations:
            print("  sources:", ", ".join(f"[{c.jira_key}]" for c in resp.citations[:5]))


if __name__ == "__main__":
    main()
