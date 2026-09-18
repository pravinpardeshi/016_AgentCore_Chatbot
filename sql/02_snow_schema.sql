-- ============================================================================
-- Multi-source KB, file 02: ServiceNow tables + application onboarding
-- Run AFTER sql/01_schema.sql:
--   psql "$DATABASE_URL" -f sql/01_schema.sql
--   psql "$DATABASE_URL" -f sql/02_snow_schema.sql
-- Embedding model: amazon.titan-embed-text-v2 (1024 dims) — same as JIRA.
-- ============================================================================

-- 1. Application registry -----------------------------------------------------
-- One row per onboarded application. An app may use JIRA, ServiceNow, or both:
--   * JIRA-only  : jira_projects = '{PROJ}', snow_groups = '{}'
--   * SNOW-only  : jira_projects = '{}',    snow_groups = '{APP-SUPPORT}'
--   * Both       : fill in both arrays
-- KB rows carry app_code so one shared table set serves all apps (pattern A).
CREATE TABLE IF NOT EXISTS applications (
    app_code        TEXT PRIMARY KEY,              -- e.g. 'CHECKOUT', 'PAYMENTS'
    name            TEXT NOT NULL,
    description     TEXT,
    jira_projects   TEXT[] NOT NULL DEFAULT '{}',  -- JIRA project keys feeding this app
    snow_groups     TEXT[] NOT NULL DEFAULT '{}',  -- ServiceNow assignment groups
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Backfill scope columns on the JIRA side (nullable → old rows keep working).
ALTER TABLE jira_tickets
    ADD COLUMN IF NOT EXISTS app_code TEXT REFERENCES applications(app_code);
ALTER TABLE ticket_chunks
    ADD COLUMN IF NOT EXISTS app_code TEXT;
ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'jira';

CREATE INDEX IF NOT EXISTS idx_tickets_app_code ON jira_tickets (app_code);
CREATE INDEX IF NOT EXISTS idx_chunks_app_code  ON ticket_chunks (app_code);

-- 2. ServiceNow incident table (one row per closed incident) ------------------
CREATE TABLE IF NOT EXISTS snow_incidents (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    number              TEXT NOT NULL UNIQUE,      -- e.g. INC0010234
    sys_id              TEXT NOT NULL UNIQUE,      -- ServiceNow sys_id
    app_code            TEXT REFERENCES applications(app_code),
    assignment_group    TEXT,                      -- e.g. 'APP-SUPPORT'
    category            TEXT,
    subcategory         TEXT,
    priority            TEXT,                      -- 1-Critical .. 5-Planning
    state               TEXT NOT NULL,             -- 6=Resolved, 7=Closed
    short_description   TEXT NOT NULL,
    description         TEXT,
    close_notes         TEXT,
    work_notes          TEXT,                      -- journal trail (truncated at ingest)
    caller              TEXT,
    assigned_to         TEXT,
    opened_at           TIMESTAMPTZ,
    closed_at           TIMESTAMPTZ,
    snow_url            TEXT,
    raw_json            JSONB,
    created_row_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_row_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_snow_app_code  ON snow_incidents (app_code);
CREATE INDEX IF NOT EXISTS idx_snow_group      ON snow_incidents (assignment_group);
CREATE INDEX IF NOT EXISTS idx_snow_state      ON snow_incidents (state);
CREATE INDEX IF NOT EXISTS idx_snow_number_trgm ON snow_incidents USING gin (number gin_trgm_ops);

ALTER TABLE snow_incidents
    ADD COLUMN IF NOT EXISTS search_tsv tsvector
    GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(short_description,'') || ' ' ||
            coalesce(description,'') || ' ' ||
            coalesce(close_notes,'')
        )
    ) STORED;
CREATE INDEX IF NOT EXISTS idx_snow_search_tsv ON snow_incidents USING gin (search_tsv);

-- 3. ServiceNow chunk table (mirrors ticket_chunks) ---------------------------
CREATE TABLE IF NOT EXISTS snow_chunks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id     UUID NOT NULL REFERENCES snow_incidents(id) ON DELETE CASCADE,
    number          TEXT NOT NULL,                 -- denormalised INC number
    app_code        TEXT,                          -- denormalised app scope
    chunk_index     INTEGER NOT NULL,
    section         TEXT NOT NULL DEFAULT 'description',  -- short_description | description | work_notes | close_notes
    content         TEXT NOT NULL,
    content_tsv     tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(content,''))) STORED,
    embedding       vector(1024),
    embedding_model TEXT NOT NULL DEFAULT 'amazon.titan-embed-text-v2',
    token_count     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (incident_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_snow_chunks_incident ON snow_chunks (incident_id);
CREATE INDEX IF NOT EXISTS idx_snow_chunks_number   ON snow_chunks (number);
CREATE INDEX IF NOT EXISTS idx_snow_chunks_app      ON snow_chunks (app_code);
CREATE INDEX IF NOT EXISTS idx_snow_chunks_tsv      ON snow_chunks USING gin (content_tsv);
CREATE INDEX IF NOT EXISTS idx_snow_chunks_hnsw
    ON snow_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

DROP TRIGGER IF EXISTS trg_snow_touch ON snow_incidents;
CREATE TRIGGER trg_snow_touch
    BEFORE UPDATE ON snow_incidents
    FOR EACH ROW EXECUTE FUNCTION touch_updated_row_at();

-- 4. ServiceNow hybrid search (mirrors hybrid_ticket_search) ------------------
CREATE OR REPLACE FUNCTION hybrid_snow_search(
    p_query_embedding vector(1024),
    p_query_text      TEXT,
    p_top_k           INTEGER DEFAULT 8,
    p_vector_weight   FLOAT   DEFAULT 0.7,
    p_fts_weight      FLOAT   DEFAULT 0.3,
    p_app_code        TEXT    DEFAULT NULL,
    p_group_filter    TEXT    DEFAULT NULL
) RETURNS TABLE (
    chunk_id        UUID,
    number          TEXT,
    app_code        TEXT,
    assignment_group TEXT,
    short_description TEXT,
    section         TEXT,
    chunk_index     INTEGER,
    content         TEXT,
    snow_url        TEXT,
    vector_score    FLOAT,
    fts_score       FLOAT,
    combined_score  FLOAT
) LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN QUERY
    WITH vector_ranked AS (
        SELECT c.id, 1 - (c.embedding <=> p_query_embedding) AS v_score
        FROM snow_chunks c
        JOIN snow_incidents i ON i.id = c.incident_id
        WHERE c.embedding IS NOT NULL
          AND (p_app_code IS NULL OR i.app_code = p_app_code)
          AND (p_group_filter IS NULL OR i.assignment_group = p_group_filter)
        ORDER BY c.embedding <=> p_query_embedding
        LIMIT (p_top_k * 4)
    ),
    fts_ranked AS (
        SELECT c.id, ts_rank_cd(c.content_tsv, plainto_tsquery('english', p_query_text)) AS f_score
        FROM snow_chunks c
        JOIN snow_incidents i ON i.id = c.incident_id
        WHERE c.content_tsv @@ plainto_tsquery('english', p_query_text)
          AND (p_app_code IS NULL OR i.app_code = p_app_code)
          AND (p_group_filter IS NULL OR i.assignment_group = p_group_filter)
        ORDER BY f_score DESC
        LIMIT (p_top_k * 4)
    ),
    merged AS (
        SELECT id FROM vector_ranked UNION SELECT id FROM fts_ranked
    )
    SELECT c.id, i.number, i.app_code, i.assignment_group, i.short_description,
        c.section, c.chunk_index, c.content, i.snow_url,
        COALESCE(v.v_score, 0)::FLOAT, COALESCE(f.f_score, 0)::FLOAT,
        (COALESCE(v.v_score, 0) * p_vector_weight + COALESCE(f.f_score, 0) * p_fts_weight)::FLOAT
    FROM merged m
    JOIN snow_chunks c ON c.id = m.id
    JOIN snow_incidents i ON i.id = c.incident_id
    LEFT JOIN vector_ranked v ON v.id = m.id
    LEFT JOIN fts_ranked f ON f.id = m.id
    ORDER BY combined_score DESC
    LIMIT p_top_k;
END;
$$;

CREATE OR REPLACE FUNCTION match_snow_chunks(
    p_query_embedding vector(1024),
    p_top_k           INTEGER DEFAULT 8,
    p_app_code        TEXT DEFAULT NULL,
    p_group_filter    TEXT DEFAULT NULL
) RETURNS TABLE (
    chunk_id    UUID, number TEXT, app_code TEXT, assignment_group TEXT,
    short_description TEXT, section TEXT, chunk_index INTEGER,
    content TEXT, snow_url TEXT, similarity FLOAT
) LANGUAGE sql STABLE AS $$
    SELECT c.id, i.number, i.app_code, i.assignment_group, i.short_description,
        c.section, c.chunk_index, c.content, i.snow_url,
        (1 - (c.embedding <=> p_query_embedding))::FLOAT
    FROM snow_chunks c
    JOIN snow_incidents i ON i.id = c.incident_id
    WHERE c.embedding IS NOT NULL
      AND (p_app_code IS NULL OR i.app_code = p_app_code)
      AND (p_group_filter IS NULL OR i.assignment_group = p_group_filter)
    ORDER BY c.embedding <=> p_query_embedding
    LIMIT p_top_k;
$$;

-- 5. Unified cross-source search (JIRA + ServiceNow) --------------------------
-- p_source_filter: 'jira' | 'snow' | NULL (both). Scores are comparable because
-- both sides use the same embedding model and the same 0.7/0.3 weighting.
CREATE OR REPLACE FUNCTION hybrid_kb_search(
    p_query_embedding vector(1024),
    p_query_text      TEXT,
    p_top_k           INTEGER DEFAULT 8,
    p_vector_weight   FLOAT   DEFAULT 0.7,
    p_fts_weight      FLOAT   DEFAULT 0.3,
    p_source_filter   TEXT    DEFAULT NULL,
    p_app_code        TEXT    DEFAULT NULL,
    p_project_filter  TEXT    DEFAULT NULL,
    p_group_filter    TEXT    DEFAULT NULL
) RETURNS TABLE (
    source          TEXT,
    ref_key         TEXT,
    grouping        TEXT,
    summary         TEXT,
    section         TEXT,
    chunk_index     INTEGER,
    content         TEXT,
    url             TEXT,
    vector_score    FLOAT,
    fts_score       FLOAT,
    combined_score  FLOAT
) LANGUAGE sql STABLE AS $$
    (
        SELECT 'jira'::TEXT AS source, j.jira_key AS ref_key, j.project AS grouping,
            j.summary, j.section, j.chunk_index, j.content, j.jira_url AS url,
            j.vector_score, j.fts_score, j.combined_score
        FROM hybrid_ticket_search(
            p_query_embedding, p_query_text, (p_top_k * 2),
            p_vector_weight, p_fts_weight, p_project_filter
        ) j
        JOIN ticket_chunks c ON c.id = j.chunk_id
        WHERE (p_source_filter IS NULL OR p_source_filter = 'jira')
          AND (p_app_code IS NULL OR c.app_code = p_app_code)
    )
    UNION ALL
    (
        SELECT 'snow'::TEXT, s.number, s.assignment_group,
            s.short_description, s.section, s.chunk_index, s.content, s.snow_url,
            s.vector_score, s.fts_score, s.combined_score
        FROM hybrid_snow_search(
            p_query_embedding, p_query_text, (p_top_k * 2),
            p_vector_weight, p_fts_weight, p_app_code, p_group_filter
        ) s
        WHERE (p_source_filter IS NULL OR p_source_filter = 'snow')
    )
    ORDER BY combined_score DESC
    LIMIT p_top_k;
$$;

-- 6. Unified stats --------------------------------------------------------------
CREATE OR REPLACE VIEW v_kb_stats AS
SELECT 'jira'::TEXT AS source, t.project AS grouping, t.app_code,
    count(DISTINCT t.id) AS records, count(c.id) AS chunks,
    count(c.embedding) AS embedded_chunks
FROM jira_tickets t LEFT JOIN ticket_chunks c ON c.ticket_id = t.id
GROUP BY t.project, t.app_code
UNION ALL
SELECT 'snow'::TEXT, i.assignment_group, i.app_code,
    count(DISTINCT i.id), count(c.id), count(c.embedding)
FROM snow_incidents i LEFT JOIN snow_chunks c ON c.incident_id = i.id
GROUP BY i.assignment_group, i.app_code;

-- ============================================================================
-- 7. Onboarding playbook for new applications
-- ----------------------------------------------------------------------------
-- Pattern A (RECOMMENDED, zero DDL): register the app, ingest with its code.
--   INSERT INTO applications (app_code, name, jira_projects, snow_groups)
--   VALUES ('CHECKOUT', 'Checkout service', '{CHK}', '{CHK-SUPPORT}');
--   -- JIRA ingest:  JIRA_APP_CODE=CHECKOUT python -m jira_agents.ingestion.pipeline --source jira
--   -- SNOW ingest:  SNOW_APP_CODE=CHECKOUT python -m jira_agents.ingestion.pipeline --source snow
--   Rows land in the shared tables scoped by app_code; retrieval filters on it.
--
-- Pattern B (isolated per-app tables, e.g. strict data residency): clone the
-- shared tables, then point a dedicated pipeline at them:
--   CREATE TABLE kb_checkout_jira   (LIKE jira_tickets INCLUDING ALL);
--   CREATE TABLE kb_checkout_chunks (LIKE ticket_chunks INCLUDING ALL);
--   -- copy hybrid_ticket_search() to hybrid_checkout_search() with the new
--   -- table names, and add the agent-side query in db/retriever.py.
-- Prefer A unless compliance forces physical separation.
-- ============================================================================
