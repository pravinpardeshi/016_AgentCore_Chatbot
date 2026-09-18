"""Ingestion pipeline: JIRA / ServiceNow -> Postgres -> chunk -> Bedrock embed -> pgvector.

Usage:
    python -m jira_agents.ingestion.pipeline --source jira --max 200
    python -m jira_agents.ingestion.pipeline --source snow --max 200
    python -m jira_agents.ingestion.pipeline --source all  --max 200   # both sources
    python -m jira_agents.ingestion.pipeline --source jira --max 200 --no-embed
    python -m jira_agents.ingestion.pipeline --backfill-embeddings     # both chunk tables
"""
from __future__ import annotations

import argparse
import json
import uuid

from pgvector.psycopg import register_vector

from ..config import get_settings
from ..db.connection import get_direct_conn
from .chunker import chunk_snow_incident, chunk_ticket
from .embedder import Embedder
from .jira_client import JiraClient, JiraTicket
from .snow_client import ServiceNowClient, SnowIncident


def upsert_ticket(cur, t: JiraTicket, app_code: str | None) -> str:
    cur.execute(
        """
        INSERT INTO jira_tickets
            (jira_key, project, issue_type, status, priority, summary,
             description, resolution, assignee, reporter, labels, components,
             created_at, updated_at, resolved_at, jira_url, raw_json, app_code)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s::timestamptz, %s::timestamptz, %s::timestamptz, %s, %s::jsonb, %s)
        ON CONFLICT (jira_key) DO UPDATE SET
            project=EXCLUDED.project, issue_type=EXCLUDED.issue_type,
            status=EXCLUDED.status, priority=EXCLUDED.priority,
            summary=EXCLUDED.summary, description=EXCLUDED.description,
            resolution=EXCLUDED.resolution, assignee=EXCLUDED.assignee,
            reporter=EXCLUDED.reporter, labels=EXCLUDED.labels,
            components=EXCLUDED.components, created_at=EXCLUDED.created_at,
            updated_at=EXCLUDED.updated_at, resolved_at=EXCLUDED.resolved_at,
            jira_url=EXCLUDED.jira_url, raw_json=EXCLUDED.raw_json,
            app_code=EXCLUDED.app_code
        RETURNING id
        """,
        (
            t.key, t.project, t.issue_type, t.status, t.priority, t.summary,
            t.description, t.resolution, t.assignee, t.reporter,
            t.labels, t.components, t.created, t.updated, t.resolved,
            t.url, json.dumps(t.raw), app_code,
        ),
    )
    row = cur.fetchone()
    return str(row["id"])


def write_chunks(cur, ticket_id: str, jira_key: str, t: JiraTicket,
                 embeddings: list[list[float]] | None, settings,
                 app_code: str | None = None) -> int:
    chunks = chunk_ticket(t.summary, t.description, t.resolution,
                          size=settings.chunk_size, overlap=settings.chunk_overlap)
    # Replace existing chunks for idempotent re-ingestion
    cur.execute("DELETE FROM ticket_chunks WHERE ticket_id = %s", (ticket_id,))
    n = 0
    for i, ch in enumerate(chunks):
        emb = embeddings[i] if embeddings else None
        cur.execute(
            """
            INSERT INTO ticket_chunks
                (ticket_id, jira_key, chunk_index, section, content, embedding, embedding_model, token_count, app_code)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (ticket_id, jira_key, ch.index, ch.section, ch.content, emb,
             settings.embedding_model_id, len(ch.content) // 4, app_code),
        )
        n += 1
    return n


def upsert_snow_incident(cur, inc: SnowIncident, app_code: str | None) -> str:
    cur.execute(
        """
        INSERT INTO snow_incidents
            (number, sys_id, app_code, assignment_group, category, subcategory,
             priority, state, short_description, description, close_notes, work_notes,
             caller, assigned_to, opened_at, closed_at, snow_url, raw_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s::timestamptz, %s::timestamptz, %s, %s::jsonb)
        ON CONFLICT (number) DO UPDATE SET
            sys_id=EXCLUDED.sys_id, app_code=EXCLUDED.app_code,
            assignment_group=EXCLUDED.assignment_group, category=EXCLUDED.category,
            subcategory=EXCLUDED.subcategory, priority=EXCLUDED.priority,
            state=EXCLUDED.state, short_description=EXCLUDED.short_description,
            description=EXCLUDED.description, close_notes=EXCLUDED.close_notes,
            work_notes=EXCLUDED.work_notes, caller=EXCLUDED.caller,
            assigned_to=EXCLUDED.assigned_to, opened_at=EXCLUDED.opened_at,
            closed_at=EXCLUDED.closed_at, snow_url=EXCLUDED.snow_url,
            raw_json=EXCLUDED.raw_json
        RETURNING id
        """,
        (
            inc.number, inc.sys_id, app_code, inc.assignment_group, inc.category,
            inc.subcategory, inc.priority, inc.state, inc.short_description,
            inc.description, inc.close_notes, inc.work_notes, inc.caller,
            inc.assigned_to, inc.opened, inc.closed, inc.url, json.dumps(inc.raw),
        ),
    )
    return str(cur.fetchone()["id"])


def write_snow_chunks(cur, incident_id: str, inc: SnowIncident,
                      embeddings: list[list[float]] | None, settings,
                      app_code: str | None = None) -> int:
    chunks = chunk_snow_incident(inc.short_description, inc.description,
                                 inc.work_notes, inc.close_notes,
                                 size=settings.chunk_size, overlap=settings.chunk_overlap)
    cur.execute("DELETE FROM snow_chunks WHERE incident_id = %s", (incident_id,))
    n = 0
    for i, ch in enumerate(chunks):
        emb = embeddings[i] if embeddings else None
        cur.execute(
            """
            INSERT INTO snow_chunks
                (incident_id, number, chunk_index, section, content, embedding,
                 embedding_model, token_count, app_code)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (incident_id, inc.number, ch.index, ch.section, ch.content, emb,
             settings.embedding_model_id, len(ch.content) // 4, app_code),
        )
        n += 1
    return n


def _new_run(cur, source: str, query: str) -> str:
    run_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO ingestion_runs (id, source, jql) VALUES (%s, %s, %s)",
        (run_id, source, query),
    )
    return run_id


def _finish_run(cur, run_id: str, fetched: int, upserted: int, chunks: int) -> None:
    cur.execute(
        """UPDATE ingestion_runs SET finished_at=now(), tickets_fetched=%s,
           tickets_upserted=%s, chunks_written=%s, status='succeeded' WHERE id=%s""",
        (fetched, upserted, chunks, run_id),
    )


def _fail_run(cur, run_id: str, error: str) -> None:
    cur.execute(
        "UPDATE ingestion_runs SET finished_at=now(), status='failed', error=%s WHERE id=%s",
        (error, run_id),
    )


def run_jira_pipeline(max_tickets: int | None = None, with_embeddings: bool = True) -> dict:
    settings = get_settings()
    max_tickets = max_tickets or settings.jira_max_tickets
    app_code = settings.jira_app_code or None
    client = JiraClient(settings.jira_base_url, settings.jira_email, settings.jira_api_token)
    embedder = Embedder(settings.embedding_model_id, settings.aws_region) if with_embeddings else None

    print(f"[jira] Fetching up to {max_tickets} tickets | JQL: {settings.jira_jql_closed} | app={app_code}")
    tickets = client.search_closed(settings.jira_jql_closed, max_results=max_tickets)
    print(f"[jira] Fetched {len(tickets)} tickets from JIRA")

    conn = get_direct_conn()
    register_vector(conn)
    upserted = 0
    chunk_total = 0
    with conn.cursor() as cur:
        run_id = _new_run(cur, "jira", settings.jira_jql_closed)
    conn.commit()
    try:
        for t in tickets:
            chunks = chunk_ticket(t.summary, t.description, t.resolution,
                                  size=settings.chunk_size, overlap=settings.chunk_overlap)
            embeddings = embedder.embed_texts([c.content for c in chunks]) if (embedder and chunks) else None
            with conn.cursor() as cur:
                tid = upsert_ticket(cur, t, app_code)
                chunk_total += write_chunks(cur, tid, t.key, t, embeddings, settings, app_code)
            conn.commit()
            upserted += 1
            if upserted % 25 == 0:
                print(f"  ... {upserted}/{len(tickets)} upserted")
        with conn.cursor() as cur:
            _finish_run(cur, run_id, len(tickets), upserted, chunk_total)
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        with conn.cursor() as cur:
            _fail_run(cur, run_id, str(e))
        conn.commit()
        raise
    finally:
        conn.close()
    print(f"[jira] DONE: {upserted} tickets, {chunk_total} chunks")
    return {"source": "jira", "tickets": upserted, "chunks": chunk_total, "run_id": run_id}


def run_snow_pipeline(max_incidents: int | None = None, with_embeddings: bool = True) -> dict:
    settings = get_settings()
    max_incidents = max_incidents or settings.snow_max_incidents
    app_code = settings.snow_app_code or None
    client = ServiceNowClient(settings.snow_instance_url, settings.snow_username,
                              settings.snow_password)
    embedder = Embedder(settings.embedding_model_id, settings.aws_region) if with_embeddings else None

    print(f"[snow] Fetching up to {max_incidents} incidents | query: {settings.snow_encoded_query} | app={app_code}")
    incidents = client.search_closed(settings.snow_encoded_query, max_results=max_incidents)
    print(f"[snow] Fetched {len(incidents)} incidents from ServiceNow")

    conn = get_direct_conn()
    register_vector(conn)
    upserted = 0
    chunk_total = 0
    with conn.cursor() as cur:
        run_id = _new_run(cur, "snow", settings.snow_encoded_query)
    conn.commit()
    try:
        for inc in incidents:
            chunks = chunk_snow_incident(inc.short_description, inc.description,
                                         inc.work_notes, inc.close_notes,
                                         size=settings.chunk_size, overlap=settings.chunk_overlap)
            embeddings = embedder.embed_texts([c.content for c in chunks]) if (embedder and chunks) else None
            with conn.cursor() as cur:
                iid = upsert_snow_incident(cur, inc, app_code)
                chunk_total += write_snow_chunks(cur, iid, inc, embeddings, settings, app_code)
            conn.commit()
            upserted += 1
            if upserted % 25 == 0:
                print(f"  ... {upserted}/{len(incidents)} upserted")
        with conn.cursor() as cur:
            _finish_run(cur, run_id, len(incidents), upserted, chunk_total)
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        with conn.cursor() as cur:
            _fail_run(cur, run_id, str(e))
        conn.commit()
        raise
    finally:
        conn.close()
    print(f"[snow] DONE: {upserted} incidents, {chunk_total} chunks")
    return {"source": "snow", "tickets": upserted, "chunks": chunk_total, "run_id": run_id}


# Backwards-compatible alias (defaults to the JIRA pipeline).
def run_pipeline(max_tickets: int | None = None, with_embeddings: bool = True) -> dict:
    return run_jira_pipeline(max_tickets=max_tickets, with_embeddings=with_embeddings)


def backfill_embeddings(batch_size: int = 32) -> int:
    """Embed NULL rows in BOTH chunk tables (e.g. ingested with --no-embed)."""
    settings = get_settings()
    embedder = Embedder(settings.embedding_model_id, settings.aws_region)
    conn = get_direct_conn()
    register_vector(conn)
    total = 0
    for table in ("ticket_chunks", "snow_chunks"):
        while True:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, content FROM {table} WHERE embedding IS NULL LIMIT %s",
                    (batch_size,),
                )
                rows = cur.fetchall()
            if not rows:
                break
            vecs = embedder.embed_texts([r["content"] for r in rows])
            with conn.cursor() as cur:
                for r, v in zip(rows, vecs):
                    cur.execute(f"UPDATE {table} SET embedding=%s WHERE id=%s", (v, r["id"]))
            conn.commit()
            total += len(rows)
            print(f"  [{table}] backfilled {total} total ...")
    conn.close()
    print(f"Backfill complete: {total} chunks")
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest closed JIRA tickets / ServiceNow incidents into pgvector")
    ap.add_argument("--source", choices=["jira", "snow", "all"], default="jira",
                    help="KB source to ingest (default: jira)")
    ap.add_argument("--max", type=int, default=None)
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--backfill-embeddings", action="store_true")
    args = ap.parse_args()
    if args.backfill_embeddings:
        backfill_embeddings()
    elif args.source == "jira":
        run_jira_pipeline(max_tickets=args.max, with_embeddings=not args.no_embed)
    elif args.source == "snow":
        run_snow_pipeline(max_incidents=args.max, with_embeddings=not args.no_embed)
    else:
        run_jira_pipeline(max_tickets=args.max, with_embeddings=not args.no_embed)
        run_snow_pipeline(max_incidents=args.max, with_embeddings=not args.no_embed)


if __name__ == "__main__":
    main()
