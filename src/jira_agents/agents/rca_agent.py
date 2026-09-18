"""RCA Agent — PLACEHOLDER.

Future role: given a NEW incident + similar closed records (JIRA + ServiceNow),
produce a root-cause hypothesis tree (5-whys / fishbone) with evidence links.

TODO to implement:
  1. Reuse KnowledgeAgent.retrieve() for similar past records (both sources).
  2. Claude prompt: output {hypotheses[], evidence per hypothesis, most_likely, tests_to_confirm}.
  3. Optional: query logs/metrics tools via Strands/MCP for corroboration.
"""
from __future__ import annotations

from .base import AgentRequest, AgentResponse


class RCAAgent:
    name = "rca"

    def invoke(self, req: AgentRequest) -> AgentResponse:
        return AgentResponse(
            answer=(
                "[RCAAgent placeholder] Not yet implemented. "
                "Design: retrieve similar closed RCAs, then prompt Claude for "
                "ranked root-cause hypotheses with evidence. "
                f"Received: {req.message[:200]}"
            ),
            agent=self.name,
        )
