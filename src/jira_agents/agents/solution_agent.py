"""Solution Recommendation Agent — fix plans grounded ONLY in past resolutions.

Every recommended step must trace to a cited record's resolution/close-notes.
If no record supports a fix, the agent reports that (plus escalation guidance)
instead of authoring a fix from general knowledge. Read-only: it never writes
back to JIRA/ServiceNow; a human approves and applies the plan.
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

SYSTEM = """You recommend a fix for a NEW incident using ONLY the resolutions in similar
past records from the knowledge base (JIRA `resolution`, ServiceNow `close_notes`,
plus supporting `work_notes`).

Output exactly these sections:
1. Recommended fix — numbered steps in execution order. EVERY step MUST cite the
   record(s) it comes from ([KEY] on each step). Preserve commands, config keys,
   and values exactly as the records state them; do not normalise or improve them.
2. Workaround — only if a record states one (cite it); otherwise say
   "No workaround in the knowledge base".
3. Rollback plan — only if a record states one; otherwise say
   "No rollback plan in the knowledge base".
4. Risks & caveats — stated in the records (cite each).
5. Gaps — what is unproven for THIS incident and needs human verification before acting.

Hard rules: if no record supports a fix, output "Not enough evidence in the
knowledge base for a fix recommendation", name the closest records found, and
recommend escalation with the evidence bundle. NEVER author fix steps from your
own knowledge."""

EXTRA_TEMPLATE = ("\n\nUpstream context (use as context only, not as evidence):\n"
                  "{context}\n")


class SolutionAgent:
    name = "solution"
    default_top_k = 8

    def invoke(self, req: AgentRequest) -> AgentResponse:
        chunks = retrieve_chunks(req, self.default_top_k)
        if not chunks:
            return insufficient(self.name, "recommend a fix for this incident")
        user_text = f"Similar past records:\n{build_context(chunks)}\n\nNEW incident:\n{req.message}"
        upstream: list[str] = []
        for key in ("triage", "rca"):
            val = (req.extra or {}).get(key)
            if val:
                upstream.append(f"{key.upper()}:\n{str(val)[:1500]}")
        if upstream:
            user_text += EXTRA_TEMPLATE.format(context="\n\n".join(upstream))
        answer = call_claude(SYSTEM, user_text)
        return AgentResponse(answer=answer, agent=self.name,
                             citations=citations_from(chunks), raw_context=raw_from(chunks))
