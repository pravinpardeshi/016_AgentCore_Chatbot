"""Hybrid retrieval used by Knowledge Agent (and later RCA / Solution agents).

Sources: 'jira' (ticket_chunks) + 'snow' (snow_chunks). Use kb_hybrid_search()
for cross-source queries, or the per-source functions to scope to one system.
"""
from __future__ import annotations

from dataclasses import dataclass

from pgvector.psycopg import register_vector

from ..config import get_settings
from .connection import get_conn


@dataclass
class RetrievedChunk:
    chunk_id: str
    jira_key: str   # record key: JIRA key or SNOW number
    project: str    # grouping: JIRA project or SNOW assignment group
    summary: str
    section: str
    chunk_index: int
    content: str
    jira_url: str | None
    vector_score: float = 0.0
    fts_score: float = 0.0
    combined_score: float = 0.0
    source: str = "jira"  # 'jira' | 'snow'


def _row_to_chunk(r: dict, source: str = "jira") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(r.get("chunk_id")),
        jira_key=r.get("ref_key", r.get("jira_key", r.get("number", ""))) or "",
        project=r.get("grouping", r.get("project", r.get("assignment_group", ""))) or "",
        summary=r.get("summary", r.get("short_description", "")) or "",
        section=r.get("section") or "body",
        chunk_index=r.get("chunk_index") or 0,
        content=r["content"],
        jira_url=r.get("url", r.get("jira_url", r.get("snow_url"))),
        vector_score=float(r.get("vector_score") or 0),
        fts_score=float(r.get("fts_score") or 0),
        combined_score=float(r.get("combined_score") or 0),
        source=r.get("source", source),
    )


def hybrid_search(
    query_embedding: list[float],
    query_text: str,
    top_k: int | None = None,
    project_filter: str | None = None,
) -> list[RetrievedChunk]:
    """Call the `hybrid_ticket_search` SQL function. Falls back to pure vector."""
    settings = get_settings()
    top_k = top_k or settings.top_k
    with get_conn() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM hybrid_ticket_search(
                    %s::vector, %s, %s, %s, %s, %s
                )
                """,
                (
                    query_embedding,
                    query_text,
                    top_k,
                    settings.vector_weight,
                    settings.fts_weight,
                    project_filter,
                ),
            )
            rows = cur.fetchall()
    out: list[RetrievedChunk] = []
    for r in rows:
        out.append(
            RetrievedChunk(
                chunk_id=str(r["chunk_id"]),
                jira_key=r["jira_key"],
                project=r["project"],
                summary=r.get("summary") or "",
                section=r.get("section") or "body",
                chunk_index=r.get("chunk_index") or 0,
                content=r["content"],
                jira_url=r.get("jira_url"),
                vector_score=float(r.get("vector_score") or 0),
                fts_score=float(r.get("fts_score") or 0),
                combined_score=float(r.get("combined_score") or 0),
                source="jira",
            )
        )
    return out


def vector_search(
    query_embedding: list[float],
    top_k: int | None = None,
    project_filter: str | None = None,
) -> list[RetrievedChunk]:
    settings = get_settings()
    top_k = top_k or settings.top_k
    with get_conn() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM match_ticket_chunks(%s::vector, %s, %s)",
                (query_embedding, top_k, project_filter),
            )
            rows = cur.fetchall()
    return [
        RetrievedChunk(
            chunk_id=str(r["chunk_id"]),
            jira_key=r["jira_key"],
            project=r["project"],
            summary=r.get("summary") or "",
            section=r.get("section") or "body",
            chunk_index=r.get("chunk_index") or 0,
            content=r["content"],
            jira_url=r.get("jira_url"),
            combined_score=float(r.get("similarity") or 0),
            source="jira",
        )
        for r in rows
    ]


def snow_hybrid_search(
    query_embedding: list[float],
    query_text: str,
    top_k: int | None = None,
    app_code: str | None = None,
    group_filter: str | None = None,
) -> list[RetrievedChunk]:
    """Hybrid search over ServiceNow chunks only."""
    settings = get_settings()
    top_k = top_k or settings.top_k
    with get_conn() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM hybrid_snow_search(%s::vector, %s, %s, %s, %s, %s, %s)",
                (query_embedding, query_text, top_k, settings.vector_weight,
                 settings.fts_weight, app_code, group_filter),
            )
            rows = cur.fetchall()
    return [
        RetrievedChunk(
            chunk_id=str(r["chunk_id"]),
            jira_key=r["number"],
            project=r.get("assignment_group") or "",
            summary=r.get("short_description") or "",
            section=r.get("section") or "description",
            chunk_index=r.get("chunk_index") or 0,
            content=r["content"],
            jira_url=r.get("snow_url"),
            vector_score=float(r.get("vector_score") or 0),
            fts_score=float(r.get("fts_score") or 0),
            combined_score=float(r.get("combined_score") or 0),
            source="snow",
        )
        for r in rows
    ]


def kb_hybrid_search(
    query_embedding: list[float],
    query_text: str,
    top_k: int | None = None,
    source_filter: str | None = None,
    app_code: str | None = None,
    project_filter: str | None = None,
    group_filter: str | None = None,
) -> list[RetrievedChunk]:
    """Unified cross-source hybrid search (JIRA + ServiceNow).

    source_filter: 'jira' | 'snow' | None (both). Falls back to per-source
    vector search when the FTS query is empty / stopwords-only.
    """
    settings = get_settings()
    top_k = top_k or settings.top_k
    if source_filter == "jira" and app_code is None:
        # No app scoping needed → legacy single-source path (unchanged behaviour).
        return hybrid_search(query_embedding, query_text, top_k, project_filter)
    if source_filter == "snow":
        try:
            return snow_hybrid_search(query_embedding, query_text, top_k, app_code, group_filter)
        except Exception:
            return snow_vector_search(query_embedding, top_k, app_code, group_filter)
    with get_conn() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM hybrid_kb_search(%s::vector, %s, %s, %s, %s, %s, %s, %s, %s)",
                (query_embedding, query_text, top_k, settings.vector_weight,
                 settings.fts_weight, source_filter, app_code, project_filter, group_filter),
            )
            rows = cur.fetchall()
    if not rows and not query_text.strip():
        return kb_vector_search(query_embedding, top_k, source_filter, app_code)
    return [_row_to_chunk(r) for r in rows]


def snow_vector_search(
    query_embedding: list[float],
    top_k: int | None = None,
    app_code: str | None = None,
    group_filter: str | None = None,
) -> list[RetrievedChunk]:
    settings = get_settings()
    top_k = top_k or settings.top_k
    with get_conn() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM match_snow_chunks(%s::vector, %s, %s, %s)",
                (query_embedding, top_k, app_code, group_filter),
            )
            rows = cur.fetchall()
    return [
        RetrievedChunk(
            chunk_id=str(r["chunk_id"]),
            jira_key=r["number"],
            project=r.get("assignment_group") or "",
            summary=r.get("short_description") or "",
            section=r.get("section") or "description",
            chunk_index=r.get("chunk_index") or 0,
            content=r["content"],
            jira_url=r.get("snow_url"),
            combined_score=float(r.get("similarity") or 0),
            source="snow",
        )
        for r in rows
    ]


def kb_vector_search(
    query_embedding: list[float],
    top_k: int | None = None,
    source_filter: str | None = None,
    app_code: str | None = None,
) -> list[RetrievedChunk]:
    """Pure-vector fallback across both sources (merge + re-sort in Python)."""
    settings = get_settings()
    top_k = top_k or settings.top_k
    out: list[RetrievedChunk] = []
    if source_filter in (None, "jira"):
        out.extend(vector_search(query_embedding, top_k * 2 if source_filter is None else top_k))
    if source_filter in (None, "snow"):
        out.extend(snow_vector_search(query_embedding, top_k * 2 if source_filter is None else top_k,
                                      app_code))
    out.sort(key=lambda c: c.combined_score, reverse=True)
    return out[:top_k]
