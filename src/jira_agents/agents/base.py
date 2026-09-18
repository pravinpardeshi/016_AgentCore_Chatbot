"""Shared agent contract — every current + future agent implements this."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class AgentRequest:
    message: str
    session_id: str | None = None
    project_filter: str | None = None   # JIRA project key (e.g. 'PROJ')
    group_filter: str | None = None     # ServiceNow assignment group
    source_filter: str | None = None    # 'jira' | 'snow' | None (both)
    app_code: str | None = None         # application scope (see applications table)
    top_k: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Citation:
    jira_key: str   # record key: JIRA key (PROJ-123) or SNOW number (INC001)
    summary: str
    section: str
    jira_url: str | None = None
    score: float = 0.0
    source: str = "jira"  # 'jira' | 'snow'


@dataclass
class AgentResponse:
    answer: str
    agent: str
    citations: list[Citation] = field(default_factory=list)
    raw_context: list[dict[str, Any]] = field(default_factory=list)


class BaseAgent(Protocol):
    name: str

    def invoke(self, req: AgentRequest) -> AgentResponse: ...
