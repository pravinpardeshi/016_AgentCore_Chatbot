"""Agent registry — the extensibility point.

Add a new agent: implement `BaseAgent` (name + invoke) and register one line here.
The gateway routes `{"agent": "<name>", ...}` to the right implementation.
Default = knowledge.
"""
from __future__ import annotations

from .base import BaseAgent
from .knowledge_agent import KnowledgeAgent
from .rca_agent import RCAAgent
from .solution_agent import SolutionAgent
from .triage_agent import TriageAgent

AGENT_REGISTRY: dict[str, BaseAgent] = {
    "knowledge": KnowledgeAgent(),
    "triage": TriageAgent(),       # placeholder
    "rca": RCAAgent(),             # placeholder
    "solution": SolutionAgent(),   # placeholder
}


def get_agent(name: str | None) -> BaseAgent:
    key = (name or "knowledge").lower()
    if key not in AGENT_REGISTRY:
        valid = ", ".join(sorted(AGENT_REGISTRY))
        raise ValueError(f"Unknown agent '{name}'. Valid: {valid}")
    return AGENT_REGISTRY[key]


def list_agents() -> list[str]:
    return sorted(AGENT_REGISTRY)


def register_agent(name: str, agent: BaseAgent) -> None:
    """Runtime extension hook, e.g. `register_agent('cost', CostAgent())`."""
    AGENT_REGISTRY[name.lower()] = agent
