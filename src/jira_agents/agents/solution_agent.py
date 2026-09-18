"""Solution Recommendation Agent — PLACEHOLDER.

Future role: given a NEW incident (+ triage + RCA outputs), recommend concrete
fix steps, workarounds, and links to the closed JIRA/ServiceNow records that
resolved it.

TODO to implement:
  1. Chain: TriageAgent -> RCAAgent -> this agent (pass outputs in req.extra).
  2. Retrieve JIRA `section='resolution'` + SNOW `section='close_notes'` chunks preferentially.
  3. Claude prompt: output {recommended_fix, workaround, rollback_plan, risks, record_refs}.
  4. Human-in-the-loop approval before any write-back to JIRA / ServiceNow.
"""
from __future__ import annotations

from .base import AgentRequest, AgentResponse


class SolutionAgent:
    name = "solution"

    def invoke(self, req: AgentRequest) -> AgentResponse:
        return AgentResponse(
            answer=(
                "[SolutionAgent placeholder] Not yet implemented. "
                "Design: chain triage+rca outputs, retrieve resolution chunks, "
                "prompt Claude for fix/workaround/rollback plan. "
                f"Received: {req.message[:200]}"
            ),
            agent=self.name,
        )
