"""Triage Agent — PLACEHOLDER.

Future role: given a NEW incident (JIRA key, ServiceNow number, or pasted
summary+description), classify severity/priority, route to team, and suggest
initial next steps by comparing against the closed-record KB (JIRA + ServiceNow).

TODO to implement:
  1. Fetch the live record via JiraClient / ServiceNowClient (or accept pasted text).
  2. Embed incident -> kb_hybrid_search(top_k=10, app_code=...) for similar past records.
  3. Claude prompt: output {severity, priority, probable_component, assignee_team, confidence}.
  4. Optionally write back (JIRA set-priority/labels, SNOW update) — needs write
     scope + human approval.
"""
from __future__ import annotations

from .base import AgentRequest, AgentResponse


class TriageAgent:
    name = "triage"

    def invoke(self, req: AgentRequest) -> AgentResponse:
        return AgentResponse(
            answer=(
                "[TriageAgent placeholder] Not yet implemented. "
                "Wire it to classify a new incident's severity/priority/team "
                "using hybrid_ticket_search over closed tickets. "
                f"Received: {req.message[:200]}"
            ),
            agent=self.name,
        )
