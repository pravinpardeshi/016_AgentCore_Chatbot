-- ============================================================================
-- JIRA Knowledge Base : Aurora Postgres + pgvector schema
-- Embedding model : amazon.titan-embed-text-v2  (1024 dims)
-- Chat model      : anthropic.claude-3-5-sonnet (Bedrock)
-- Run with: psql "$DATABASE_URL" -f sql/01_schema.sql
-- ============================================================================

-- 1. Extensions --------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- trigram fallback for fuzzy JIRA key/summary search
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 2. Core ticket table (one row per closed JIRA ticket) ----------------------
CREATE TABLE IF NOT EXISTS jira_tickets (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    jira_key        TEXT NOT NULL UNIQUE,          -- e.g. PROJ-1234
    project         TEXT NOT NULL,
    issue_type      TEXT,                          -- Bug, Incident, Task, Story ...
    status          TEXT NOT NULL,                 -- Closed / Done / Resolved
    priority        TEXT,
    summary         TEXT NOT NULL,
    description     TEXT,
    resolution      TEXT,
    assignee        TEXT,
    reporter        TEXT,
    labels          TEXT[] DEFAULT '{}',
    components      TEXT[] DEFAULT '{}',
    created_at      TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ,
    jira_url        TEXT,
    raw_json        JSONB,                         -- full JIRA payload for future agents
    created_row_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_row_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tickets_project      ON jira_tickets (project);
CREATE INDEX IF NOT EXISTS idx_tickets_status       ON jira_tickets (status);
CREATE INDEX IF NOT EXISTS idx_tickets_issue_type   ON jira_tickets (issue_type);
CREATE INDEX IF NOT EXISTS idx_tickets_jira_key_trgm ON jira_tickets USING gin (jira_key gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_tickets_summary_trgm  ON jira_tickets USING gin (summary gin_trgm_ops);
-- Full-text search over summary + description + resolution
ALTER TABLE jira_tickets
    ADD COLUMN IF NOT EXISTS search_tsv tsvector
    GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(summary,'') || ' ' ||
            coalesce(description,'') || ' ' ||
            coalesce(resolution,'')
        )
    ) STORED;
CREATE INDEX IF NOT EXISTS idx_tickets_search_tsv ON jira_tickets USING gin (search_tsv);

-- 3. Chunk table (one row per embedding chunk) -------------------------------
-- Chunking lets long descriptions / comment threads fit embedding + LLM limits
-- and gives precise citations (PROJ-123 chunk #2).
CREATE TABLE IF NOT EXISTS ticket_chunks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id       UUID NOT NULL REFERENCES jira_tickets(id) ON DELETE CASCADE,
    jira_key        TEXT NOT NULL,                 -- denormalised for fast filtering
    chunk_index     INTEGER NOT NULL,
    section         TEXT NOT NULL DEFAULT 'body',  -- body | comments | resolution | summary
    content         TEXT NOT NULL,
    content_tsv     tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(content,''))) STORED,
    embedding       vector(1024),                  -- Titan v2 1024-dim. NULL until backfilled.
    embedding_model TEXT NOT NULL DEFAULT 'amazon.titan-embed-text-v2',
    token_count     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticket_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_ticket_id ON ticket_chunks (ticket_id);
CREATE INDEX IF NOT EXISTS idx_chunks_jira_key  ON ticket_chunks (jira_key);
CREATE INDEX IF NOT EXISTS idx_chunks_content_tsv ON ticket_chunks USING gin (content_tsv);

-- HNSW cosine index for fast ANN retrieval.
-- Tune m / ef_construction for recall vs build time. ef_search set per-session.
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
    ON ticket_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 4. Ingestion bookkeeping ----------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    jql             TEXT,
    tickets_fetched INTEGER DEFAULT 0,
    tickets_upserted INTEGER DEFAULT 0,
    chunks_written  INTEGER DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'running',  -- running | succeeded | failed
    error           TEXT
);

-- 5. Hybrid search function ---------------------------------------------------
-- Combines pgvector cosine similarity with Postgres full-text rank.
-- Returns top-K chunks with ticket metadata for the Knowledge Agent.
--
-- Example:
--   SELECT * FROM hybrid_ticket_search(
--       (SELECT embedding FROM ticket_chunks LIMIT 1),  -- query embedding
--       'database connection timeout',                  -- query text
--       8, 0.6, 0.4, NULL
--   );
CREATE OR REPLACE FUNCTION hybrid_ticket_search(
    p_query_embedding vector(1024),
    p_query_text      TEXT,
    p_top_k           INTEGER DEFAULT 8,
    p_vector_weight   FLOAT   DEFAULT 0.7,
    p_fts_weight      FLOAT   DEFAULT 0.3,
    p_project_filter  TEXT    DEFAULT NULL
) RETURNS TABLE (
    chunk_id        UUID,
    jira_key        TEXT,
    project         TEXT,
    issue_type      TEXT,
    summary         TEXT,
    section         TEXT,
    chunk_index     INTEGER,
    content         TEXT,
    jira_url        TEXT,
    vector_score    FLOAT,
    fts_score       FLOAT,
    combined_score  FLOAT
) LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN QUERY
    WITH vector_ranked AS (
        SELECT
            c.id,
            1 - (c.embedding <=> p_query_embedding) AS v_score
        FROM ticket_chunks c
        JOIN jira_tickets t ON t.id = c.ticket_id
        WHERE c.embedding IS NOT NULL
          AND (p_project_filter IS NULL OR t.project = p_project_filter)
        ORDER BY c.embedding <=> p_query_embedding
        LIMIT (p_top_k * 4)
    ),
    fts_ranked AS (
        SELECT
            c.id,
            ts_rank_cd(c.content_tsv, plainto_tsquery('english', p_query_text)) AS f_score
        FROM ticket_chunks c
        JOIN jira_tickets t ON t.id = c.ticket_id
        WHERE c.content_tsv @@ plainto_tsquery('english', p_query_text)
          AND (p_project_filter IS NULL OR t.project = p_project_filter)
        ORDER BY f_score DESC
        LIMIT (p_top_k * 4)
    ),
    merged AS (
        SELECT id FROM vector_ranked
        UNION
        SELECT id FROM fts_ranked
    )
    SELECT
        c.id,
        t.jira_key,
        t.project,
        t.issue_type,
        t.summary,
        c.section,
        c.chunk_index,
        c.content,
        t.jira_url,
        COALESCE(v.v_score, 0)::FLOAT,
        COALESCE(f.f_score, 0)::FLOAT,
        (COALESCE(v.v_score, 0) * p_vector_weight
         + COALESCE(f.f_score, 0) * p_fts_weight)::FLOAT
    FROM merged m
    JOIN ticket_chunks c ON c.id = m.id
    JOIN jira_tickets t ON t.id = c.ticket_id
    LEFT JOIN vector_ranked v ON v.id = m.id
    LEFT JOIN fts_ranked f ON f.id = m.id
    ORDER BY combined_score DESC
    LIMIT p_top_k;
END;
$$;

-- 6. Pure-vector fallback (when FTS query is empty / stopwords only) ---------
CREATE OR REPLACE FUNCTION match_ticket_chunks(
    p_query_embedding vector(1024),
    p_top_k           INTEGER DEFAULT 8,
    p_project_filter  TEXT DEFAULT NULL
) RETURNS TABLE (
    chunk_id    UUID,
    jira_key    TEXT,
    project     TEXT,
    summary     TEXT,
    section     TEXT,
    chunk_index INTEGER,
    content     TEXT,
    jira_url    TEXT,
    similarity  FLOAT
) LANGUAGE sql STABLE AS $$
    SELECT
        c.id, t.jira_key, t.project, t.summary,
        c.section, c.chunk_index, c.content, t.jira_url,
        (1 - (c.embedding <=> p_query_embedding))::FLOAT AS similarity
    FROM ticket_chunks c
    JOIN jira_tickets t ON t.id = c.ticket_id
    WHERE c.embedding IS NOT NULL
      AND (p_project_filter IS NULL OR t.project = p_project_filter)
    ORDER BY c.embedding <=> p_query_embedding
    LIMIT p_top_k;
$$;

-- 7. Helpful views ------------------------------------------------------------
CREATE OR REPLACE VIEW v_ticket_chunk_stats AS
SELECT
    t.project,
    count(DISTINCT t.id) AS tickets,
    count(c.id)          AS chunks,
    count(c.embedding)   AS embedded_chunks
FROM jira_tickets t
LEFT JOIN ticket_chunks c ON c.ticket_id = t.id
GROUP BY t.project;

-- 8. Row-update trigger --------------------------------------------------------
CREATE OR REPLACE FUNCTION touch_updated_row_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_row_at = now();
    RETURN NEW;
END; $$;

DROP TRIGGER IF EXISTS trg_tickets_touch ON jira_tickets;
CREATE TRIGGER trg_tickets_touch
    BEFORE UPDATE ON jira_tickets
    FOR EACH ROW EXECUTE FUNCTION touch_updated_row_at();
