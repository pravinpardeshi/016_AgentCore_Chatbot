"""FastAPI gateway — runs locally AND inside Bedrock AgentCore Runtime.

AgentCore contract:
  * POST /invocations  {"agent": "knowledge", "message": "...", "session_id": ...}
  * GET  /ping         health check (AgentCore requires it)

Local dev extras:
  * POST /chat         same as /invocations (friendlier name)
  * GET  /agents       list registered agents
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..agents.base import AgentRequest
from ..agents.registry import get_agent, list_agents

app = FastAPI(title="JIRA Knowledge Agents (AgentCore-ready)")


class InvokePayload(BaseModel):
    agent: str = Field(default="knowledge")
    message: str = Field(..., min_length=1)
    session_id: str | None = None
    project_filter: str | None = None   # JIRA project key
    group_filter: str | None = None     # ServiceNow assignment group
    source_filter: str | None = None    # 'jira' | 'snow' | None (both)
    app_code: str | None = None         # application scope (applications table)
    top_k: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


def _dispatch(p: InvokePayload) -> dict[str, Any]:
    try:
        agent = get_agent(p.agent)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if p.source_filter is not None and p.source_filter not in ("jira", "snow"):
        raise HTTPException(status_code=400, detail="source_filter must be 'jira', 'snow', or null")
    resp = agent.invoke(
        AgentRequest(
            message=p.message,
            session_id=p.session_id,
            project_filter=p.project_filter,
            group_filter=p.group_filter,
            source_filter=p.source_filter,
            app_code=p.app_code,
            top_k=p.top_k,
            extra=p.extra,
        )
    )
    return {
        "agent": resp.agent,
        "answer": resp.answer,
        "citations": [c.__dict__ for c in resp.citations],
        "context": resp.raw_context,
    }


@app.get("/ping")
def ping() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/agents")
def agents() -> dict[str, list[str]]:
    return {"agents": list_agents()}


@app.post("/invocations")
def invocations(payload: InvokePayload) -> dict[str, Any]:
    return _dispatch(payload)


@app.post("/chat")
def chat(payload: InvokePayload) -> dict[str, Any]:
    return _dispatch(payload)
