"""Shared grounded-RAG plumbing for Triage / RCA / Solution agents.

Anti-fabrication contract enforced here (not left to each prompt alone):

1. **No chunks → no model call.** `retrieve_or_insufficient()` returns an
   explicit insufficient-evidence `AgentResponse` without ever invoking Claude,
   so the model cannot invent an answer from parametric knowledge.
2. **Deterministic decoding.** All grounded calls use `temperature: 0`.
3. **Strict system preamble.** Every agent's system prompt is prefixed with
   `GROUNDING_PREAMBLE`, which forbids general-knowledge answers, requires a
   `[KEY]` citation on every factual/recommendation claim, and forces a
   "not enough evidence" verdict (plus what's missing) when the records
   don't support a conclusion.
"""
from __future__ import annotations

import json

import boto3

from ..config import get_settings
from ..db.retriever import RetrievedChunk, kb_hybrid_search, kb_vector_search
from ..ingestion.embedder import Embedder
from .base import AgentRequest, AgentResponse, Citation

GROUNDING_PREAMBLE = """You are given records from a knowledge base of closed JIRA tickets
and ServiceNow incidents. STRICT GROUNDING RULES — violating them is failure:

1. Use ONLY the provided records. Do NOT use your own general knowledge,
   training data, or assumptions to answer, diagnose, or recommend.
2. Every factual claim, diagnosis, severity judgement, and recommended step
   MUST cite the supporting record's key exactly as shown, e.g. [PROJ-123]
   for JIRA or [INC0010234] for ServiceNow. A claim without a citation is
   forbidden — omit the claim instead.
3. If the records do not contain enough evidence for a conclusion, you MUST say
   "Not enough evidence in the knowledge base" for that item, list what
   information is missing, and STOP there. Never guess, extrapolate, or fill gaps.
4. Never invent record keys, numbers, resolutions, commands, or config values.
5. Quote record keys exactly; do not merge two records' details into one claim."""

CONTEXT_TEMPLATE = "[{key}] ({source} | {grouping}) {summary}\nSection: {section}\n{content}\nURL: {url}\n"

_embedder: Embedder | None = None
_bedrock = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        s = get_settings()
        _embedder = Embedder(s.embedding_model_id, s.aws_region)
    return _embedder


def get_bedrock():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=get_settings().aws_region)
    return _bedrock


def retrieve_chunks(req: AgentRequest, default_top_k: int) -> list[RetrievedChunk]:
    """Embed + unified hybrid search with vector fallback. May return []."""
    q_emb = get_embedder().embed_query(req.message)
    try:
        return kb_hybrid_search(
            q_emb, req.message, top_k=req.top_k or default_top_k,
            source_filter=req.source_filter, app_code=req.app_code,
            project_filter=req.project_filter, group_filter=req.group_filter,
        )
    except Exception:  # FTS can fail on empty/stopword queries -> vector fallback
        return kb_vector_search(
            q_emb, top_k=req.top_k or default_top_k,
            source_filter=req.source_filter, app_code=req.app_code,
        )


def build_context(chunks: list[RetrievedChunk], max_chars: int = 2000) -> str:
    return "\n---\n".join(
        CONTEXT_TEMPLATE.format(
            key=c.jira_key, source=c.source.upper(), grouping=c.project,
            summary=c.summary, section=c.section,
            content=c.content[:max_chars], url=c.jira_url or "n/a",
        )
        for c in chunks
    )


def call_claude(system: str, user_text: str, max_tokens: int = 1500) -> str:
    """Invoke Claude with temperature 0. System prompt always grounded."""
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": 0,
        "system": f"{GROUNDING_PREAMBLE}\n\n{system}",
        "messages": [{"role": "user", "content": [{"type": "text", "text": user_text}]}],
    }
    resp = get_bedrock().invoke_model(
        modelId=get_settings().chat_model_id, body=json.dumps(body)
    )
    payload = json.loads(resp["body"].read())
    return "".join(
        b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"
    ) or "(empty model response)"


def citations_from(chunks: list[RetrievedChunk]) -> list[Citation]:
    return [
        Citation(jira_key=c.jira_key, summary=c.summary, section=c.section,
                 jira_url=c.jira_url, score=c.combined_score, source=c.source)
        for c in chunks
    ]


def raw_from(chunks: list[RetrievedChunk]) -> list[dict]:
    return [{"source": c.source, "jira_key": c.jira_key, "section": c.section,
             "score": c.combined_score, "content": c.content[:500]} for c in chunks]


def insufficient(agent_name: str, what: str) -> AgentResponse:
    """No-evidence verdict — returned WITHOUT calling the model."""
    return AgentResponse(
        answer=(
            f"Not enough evidence in the knowledge base to {what}.\n"
            "No similar closed JIRA tickets or ServiceNow incidents were retrieved.\n"
            "Suggested next steps:\n"
            "- Rephrase the incident with error messages, component names, or timelines.\n"
            "- Remove source/project/app filters to widen the search.\n"
            "- Ingest more closed records covering this area, then retry.\n"
            "I am not guessing a classification without supporting records."
        ),
        agent=agent_name,
    )
