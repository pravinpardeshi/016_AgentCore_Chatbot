"""Triage Agent — classifies a NEW incident using ONLY similar past records.

Grounded: severity/priority/owner derive from the retrieved closed JIRA tickets
and ServiceNow incidents. No similar records → insufficient-evidence verdict,
no model call, no guessing.
"""
from __future__ import annotations

from .base import AgentRequest, AgentResponse
from .grounded import (
    build_context,
    call_claude,
    citations_from,
    insufficient,
    raw_from,
    retrieve_chunks,
)

SYSTEM = """You triage a NEW incident using ONLY similar past records from the knowledge base.

Output exactly these sections:
1. Severity — P1 (critical/outage) / P2 (major degradation) / P3 (minor) / P4 (cosmetic),
   justified ONLY by how severe the cited similar records were.
2. Priority — match the priority scale used by the cited records.
3. Probable owner — team/component, taken from the assignment groups / projects
   of the cited records (name the source system for each).
4. Confidence — High / Medium / Low, based on how closely the records match
   (symptoms, component, error text). Explain in one line.
5. Evidence — bullet each cited record and what it contributes.
6. Immediate next steps — ONLY steps visible in the records (cite each).
7. Gaps — what is missing to triage with higher confidence.

If the records are too dissimilar or too few, output "Not enough evidence in the
knowledge base" for Severity/Priority/Owner instead of choosing values."""


class TriageAgent:
    name = "triage"
    default_top_k = 10

    def invoke(self, req: AgentRequest) -> AgentResponse:
        chunks = retrieve_chunks(req, self.default_top_k)
        if not chunks:
            return insufficient(self.name, "triage this incident")
        answer = call_claude(
            SYSTEM,
            f"Similar past records:\n{build_context(chunks)}\n\n"
            f"NEW incident to triage:\n{req.message}",
        )
        return AgentResponse(answer=answer, agent=self.name,
                             citations=citations_from(chunks), raw_context=raw_from(chunks))
