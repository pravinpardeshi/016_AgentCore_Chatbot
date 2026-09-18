"""Knowledge Agent — chats with the multi-source KB (JIRA + ServiceNow).

Flow: embed(question) -> hybrid_kb_search -> Bedrock Claude -> answer + citations.
"""

from __future__ import annotations

import json

import boto3

from ..config import get_settings
from ..db.retriever import kb_hybrid_search, kb_vector_search
from ..ingestion.embedder import Embedder
from .base import AgentRequest, AgentResponse, Citation

SYSTEM_PROMPT = """You are a Knowledge Agent for closed JIRA tickets and ServiceNow incidents.
Answer ONLY from the provided context. Rules:
1. If the context answers the question, summarise with concrete steps / resolution.
2. Cite every factual claim as [KEY] using the record's key exactly as shown
   (JIRA keys like [PROJ-123], ServiceNow numbers like [INC0010234]).
   Mention the source system (JIRA / ServiceNow) when mixing both.
3. If context is insufficient, say so explicitly and suggest what to search next.
4. Never invent ticket keys, incident numbers, resolutions, or code that is not in context.
5. Keep answers concise; use bullets for multi-record syntheses."""

CONTEXT_TEMPLATE = "[{key}] ({source} | {grouping}) {summary}\nSection: {section}\n{content}\nURL: {url}\n"


class KnowledgeAgent:
    name = "knowledge"

    def __init__(self):
        self.settings = get_settings()
        self._embedder: Embedder | None = None
        self._bedrock = None

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = Embedder(self.settings.embedding_model_id, self.settings.aws_region)
        return self._embedder

    @property
    def bedrock(self):
        if self._bedrock is None:
            self._bedrock = boto3.client("bedrock-runtime", region_name=self.settings.aws_region)
        return self._bedrock

    def retrieve(self, req: AgentRequest):
        q_emb = self.embedder.embed_query(req.message)
        try:
            return kb_hybrid_search(
                q_emb, req.message, top_k=req.top_k,
                source_filter=req.source_filter, app_code=req.app_code,
                project_filter=req.project_filter, group_filter=req.group_filter,
            )
        except Exception:  # FTS can fail on empty/stopword queries -> vector fallback
            return kb_vector_search(
                q_emb, top_k=req.top_k,
                source_filter=req.source_filter, app_code=req.app_code,
            )

    def build_context(self, chunks) -> str:
        return "\n---\n".join(
            CONTEXT_TEMPLATE.format(
                key=c.jira_key, source=c.source.upper(), grouping=c.project,
                summary=c.summary, section=c.section,
                content=c.content[:2000], url=c.jira_url or "n/a",
            )
            for c in chunks
        )

    def invoke(self, req: AgentRequest) -> AgentResponse:
        chunks = self.retrieve(req)
        if not chunks:
            return AgentResponse(
                answer="I found no closed tickets or incidents matching your question. "
                       "Try rephrasing or removing the source/project/app filters.",
                agent=self.name,
            )
        context = self.build_context(chunks)
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1500,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user",
                 "content": [{"type": "text",
                              "text": f"KB context (JIRA tickets + ServiceNow incidents):\n{context}\n\nQuestion: {req.message}"}]}
            ],
        }
        resp = self.bedrock.invoke_model(
            modelId=self.settings.chat_model_id, body=json.dumps(body)
        )
        payload = json.loads(resp["body"].read())
        answer = "".join(
            b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"
        ) or "(empty model response)"

        citations = [
            Citation(jira_key=c.jira_key, summary=c.summary, section=c.section,
                     jira_url=c.jira_url, score=c.combined_score, source=c.source)
            for c in chunks
        ]
        raw = [{"source": c.source, "jira_key": c.jira_key, "section": c.section,
                "score": c.combined_score, "content": c.content[:500]} for c in chunks]
        return AgentResponse(answer=answer, agent=self.name, citations=citations, raw_context=raw)
