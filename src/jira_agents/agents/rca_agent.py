"""RCA Agent — root-cause hypotheses grounded ONLY in similar past records.

Resolution evidence (JIRA `resolution`, ServiceNow `close_notes`/`work_notes`
sections) is surfaced verbatim in context; the model must cite it per hypothesis
and must declare gaps instead of theorising beyond the records.
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

SYSTEM = """You perform root-cause analysis for a NEW incident using ONLY similar past
records from the knowledge base. Weight `resolution` (JIRA) and `close_notes` /
`work_notes` (ServiceNow) sections as the primary evidence — they describe what
actually fixed past incidents.

Output exactly these sections:
1. Ranked hypotheses — each with: the hypothesised cause, the cited records that
   support it ([KEY] each), and a likelihood (High/Medium/Low) justified by
   record similarity. Order most-likely first.
2. Most likely root cause — single pick from above, or "Not enough evidence in
   the knowledge base" if nothing is well supported.
3. Tests to confirm — ONLY checks/probes mentioned in the cited records.
4. What would disprove each hypothesis — based on record details, not speculation.
5. Gaps — missing logs, timelines, or record coverage needed for certainty.

Forbidden: causes, components, or failure modes not present in the cited records."""

EXTRA_TEMPLATE = "\n\nUpstream triage output (use as context only, not as evidence):\n{triage}\n"


class RCAAgent:
    name = "rca"
    default_top_k = 10

    def invoke(self, req: AgentRequest) -> AgentResponse:
        chunks = retrieve_chunks(req, self.default_top_k)
        if not chunks:
            return insufficient(self.name, "analyse the root cause of this incident")
        user_text = f"Similar past records:\n{build_context(chunks)}\n\nNEW incident:\n{req.message}"
        triage = (req.extra or {}).get("triage")
        if triage:
            user_text += EXTRA_TEMPLATE.format(triage=str(triage)[:1500])
        answer = call_claude(SYSTEM, user_text)
        return AgentResponse(answer=answer, agent=self.name,
                             citations=citations_from(chunks), raw_context=raw_from(chunks))
