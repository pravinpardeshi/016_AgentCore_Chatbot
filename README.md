# Knowledge Agent — JIRA + ServiceNow KB on Aurora Postgres + pgvector (AgentCore)

Chat with **closed JIRA tickets and ServiceNow incidents** using
Retrieval-Augmented Generation (RAG). **Knowledge, Triage, RCA, and Solution
Recommendation** agents are all implemented — every one answers **only** from
retrieved KB chunks and refuses to guess when evidence is missing (§8.1).
Applications are onboarded via a shared `applications` registry — each app may
use JIRA, ServiceNow, or both.

```
┌─────────────┐   JQL (Closed/Done/Resolved)   ┌──────────────────────────┐
│ JIRA Cloud  │ ─────────────────────────────► │ ingestion/pipeline.py    │
│ REST API    │                                │  --source jira|snow|all  │
└─────────────┘                                │  Titan v2 (1024-dim)     │
┌─────────────┐   Encoded query (state 6,7)    │                          │
│ ServiceNow  │ ─────────────────────────────► │                          │
│ Table API   │   normalise → chunk → embed    │                          │
└─────────────┘                                └────────────┬─────────────┘
                                                            │ upsert (app_code scoped)
                                                            ▼
                                               ┌──────────────────────────┐
                                               │ Aurora Postgres          │
                                               │ + pgvector               │
                                               │ applications (registry)  │
                                               │ jira_tickets/chunks      │
                                               │ snow_incidents/chunks    │
                                               └────────────┬─────────────┘
                                                            │ hybrid_kb_search()
                                                            ▼
User ──► FastAPI gateway (/invocations, /chat) ──► registry ──┬── knowledge ✅ (Claude 3.5 Sonnet)
                                                              ├── triage    ✅ (grounded)
                                                              ├── rca       ✅ (grounded)
                                                              └── solution  ✅ (grounded)
```

## 1. Agent roster

| Agent        | Status         | File                                    | Purpose                                                        |
|--------------|----------------|-----------------------------------------|----------------------------------------------------------------|
| `knowledge`  | ✅ Implemented | `agents/knowledge_agent.py`             | Answers from closed tickets + incidents, with `[KEY]` citations. |
| `triage`     | ✅ Implemented | `agents/triage_agent.py`                | Classifies a **new** incident: severity, priority, owner — grounded in similar past records. |
| `rca`        | ✅ Implemented | `agents/rca_agent.py`                   | Ranked root-cause hypotheses with record evidence + tests.     |
| `solution`   | ✅ Implemented | `agents/solution_agent.py`              | Fix / workaround / rollback plan; every step cites a record.   |

All agents share one contract (`agents/base.py`: `AgentRequest` in,
`AgentResponse` out) and are routed by name in `agents/registry.py`.
The default agent is `knowledge`. Triage/RCA/Solution share grounded-RAG
plumbing in `agents/grounded.py` (see §8.1) and chain via `req.extra`
(`triage` → `rca` → `solution`).

## 2. Tech stack (locked choices)

| Concern            | Choice                                              |
|--------------------|-----------------------------------------------------|
| Embeddings         | Bedrock `amazon.titan-embed-text-v2`, **1024-dim**  |
| Chat LLM           | Bedrock `anthropic.claude-3-5-sonnet-20241022-v2:0` |
| Vector DB          | Aurora Postgres + `pgvector` (dev: `pgvector/pgvector:pg16` Docker) |
| Retrieval          | Hybrid per source + unified `hybrid_kb_search()` (vector 0.7 + FTS 0.3) |
| API framework      | FastAPI + uvicorn (`/ping`, `/invocations`, `/chat`, `/agents`) |
| KB source: JIRA    | JIRA Cloud REST API (`/rest/api/3/search/jql`, paginated) |
| KB source: SNOW    | ServiceNow Table API (`/api/now/table/incident`, paginated) |
| App onboarding     | `applications` registry + `app_code` scoping; per-app tables optional (§6.4) |
| Deploy target      | Bedrock AgentCore Runtime (container via ECR)       |
| Python             | 3.12+, src-layout packaging (`src/jira_agents`)     |

> Changing the embedding model changes the vector width. The schema hard-codes
> `vector(1024)` for Titan v2 — if you switch models you must alter the column
> type and re-embed (see §7 Troubleshooting).

## 3. Project structure

```
026_AgentCore_Chatbot/            ← project root (this folder)
├── .venv/                        ← local virtualenv (uv; git-ignored, not code)
├── sql/
│   ├── 01_schema.sql             ← JIRA DDL: tables, HNSW index,
│   │                               hybrid_ticket_search(), match_ticket_chunks(),
│   │                               stats view, trigger (§6)
│   └── 02_snow_schema.sql        ← ServiceNow + multi-app: applications registry,
│                                   snow_incidents/snow_chunks, hybrid_snow_search(),
│                                   hybrid_kb_search(), v_kb_stats, onboarding playbook (§6.4)
├── src/
│   └── jira_agents/              ← the single Python package
│       ├── config.py             ← every setting, reads .env + env vars (§10)
│       ├── cli.py                ← local REPL / one-shot chat (§9)
│       ├── db/
│       │   ├── connection.py     ← psycopg3 pooled + direct connections
│       │   └── retriever.py      ← kb_hybrid_search() unified + per-source
│       │                           hybrid/vector search wrappers
│       ├── ingestion/
│       │   ├── jira_client.py    ← JIRA Cloud client: paginated /search/jql,
│       │   │                       ADF rich-text → plain text, normalisation
│       │   ├── snow_client.py    ← ServiceNow client: paginated Table API,
│       │   │                       display-value normalisation
│       │   ├── chunker.py        ← section-aware chunkers (JIRA: summary/body/resolution;
│       │   │                       SNOW: short_description/description/work_notes/close_notes)
│       │   ├── embedder.py       ← Bedrock Titan v2 batch embedder
│       │   └── pipeline.py       ← --source jira|snow|all: fetch → upsert → chunk →
│       │                           embed → write; backfill mode (§7)
│       ├── agents/
│       │   ├── base.py           ← AgentRequest (+source/group/app filters) /
│       │   │                       AgentResponse / Citation (+source) / BaseAgent
│       │   ├── grounded.py       ← shared no-fabrication plumbing: no-chunks→no-model-call,
│       │   │                       temperature 0, grounding preamble (§8.1)
│       │   ├── knowledge_agent.py← RAG Q&A over both sources (§8)
│       │   ├── triage_agent.py   ← grounded severity/priority/owner (§8.2)
│       │   ├── rca_agent.py      ← grounded hypotheses + tests (§8.2)
│       │   ├── solution_agent.py ← grounded fix/workaround/rollback (§8.2)
│       │   └── registry.py       ← AGENT_REGISTRY + get_agent/list_agents/register_agent (§13)
│       └── gateway/
│           └── app.py            ← FastAPI: /ping /invocations /chat /agents (§9)
├── deploy/
│   ├── deploy_agentcore.sh       ← ECR build+push, then CreateAgentRuntime (§11)
│   └── deploy_agentcore.py       ← boto3 bedrock-agentcore-control call (§11)
├── Dockerfile                    ← AgentCore container (uvicorn on $GATEWAY_PORT)
├── docker-compose.yml            ← local pgvector Postgres (dev only)
├── pyproject.toml                ← deps; scripts: jira-ingest, jira-chat
├── .env.example                  ← copy to .env (§5, §10)
└── README.md                     ← this file
```

## 4. Prerequisites

1. **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/) (or plain `pip`).
2. **Postgres with pgvector** — local dev via Docker, prod via **Aurora Postgres**
   with the `vector` and `pg_trgm` extensions enabled.
3. **AWS credentials** with Bedrock permissions (`bedrock:InvokeModel` for the
   Titan embedding model and the Claude chat model) in your region (default
   `us-east-1`). For AgentCore deploy you additionally need ECR + AgentCore
   permissions and an execution role (§11).
4. **JIRA Cloud API token** — create at
   https://id.atlassian.com/manage-profile/security/api-tokens; you need your
   site URL (`https://<domain>.atlassian.net`), email, and the token.
5. **ServiceNow credentials** — an integration user with read access to the
   `incident` table (basic auth `SNOW_USERNAME`/`SNOW_PASSWORD`; prefer OAuth or
   a vaulted secret in production).
6. `psql` client (for running the schema files) and `docker` (for local dev DB).

## 5. Setup

```bash
# 1. virtualenv + install (from project root)
uv venv                      # or: python -m venv .venv
source .venv/bin/activate
uv pip install -e "."        # or: pip install -e "."

# 2. environment
cp .env.example .env         # then edit: DATABASE_URL, AWS_*, JIRA_*, SNOW_* (§10)

# 3a. local pgvector (development only)
docker compose up -d
psql "$DATABASE_URL" -f sql/01_schema.sql
psql "$DATABASE_URL" -f sql/02_snow_schema.sql    # ServiceNow + applications

# 3b. Aurora Postgres (production) — run the SAME files in order against the
#     cluster endpoint. The role must be able to CREATE EXTENSION vector/pg_trgm.
psql "postgresql://<user>:<pass>@<aurora-endpoint>:5432/jirakb" -f sql/01_schema.sql
psql "postgresql://<user>:<pass>@<aurora-endpoint>:5432/jirakb" -f sql/02_snow_schema.sql
```

Verify the schema loaded:

```sql
\dx                       -- vector, pg_trgm, uuid-ossp present
\d jira_tickets
\d ticket_chunks
\d snow_incidents
\d snow_chunks
SELECT * FROM v_ticket_chunk_stats;
SELECT * FROM v_kb_stats;   -- both sources
```

## 6. Database reference (`sql/01_schema.sql` + `sql/02_snow_schema.sql`)

Two files, run **in order** (`01` then `02`). Both idempotent
(`IF NOT EXISTS` throughout) — safe to re-run. `02` only *adds* (new tables,
new functions, `app_code`/`source` columns on `01` tables), so existing JIRA
data keeps working untouched.

### 6.1 Tables

**`jira_tickets`** — one row per closed ticket (upserted on `jira_key`).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | internal |
| `jira_key` | TEXT UNIQUE | e.g. `PROJ-1234` |
| `project`, `issue_type`, `status`, `priority` | TEXT | filterable; indexes on project/status/type |
| `summary`, `description`, `resolution` | TEXT | source text for chunks |
| `assignee`, `reporter` | TEXT | display names |
| `labels`, `components` | TEXT[] | |
| `created_at`, `updated_at`, `resolved_at` | TIMESTAMPTZ | from JIRA fields |
| `jira_url` | TEXT | deep link, surfaced in citations |
| `raw_json` | JSONB | full JIRA payload (feeds future agents) |
| `search_tsv` | tsvector (generated, STORED) | `summary+description+resolution`, GIN-indexed |
| `created_row_at`, `updated_row_at` | TIMESTAMPTZ | row bookkeeping; trigger keeps `updated_row_at` fresh |

**`ticket_chunks`** — one row per embedding chunk (citations point here).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | returned as `chunk_id` |
| `ticket_id` | UUID FK → tickets, CASCADE | re-ingest deletes + rewrites chunks |
| `jira_key` | TEXT (denormalised) | fast filtering without a join |
| `chunk_index` | INT | global order within ticket; UNIQUE per ticket |
| `section` | TEXT | `summary` \| `body` \| `resolution` |
| `content` | TEXT | the embedded text |
| `content_tsv` | tsvector (generated) | GIN-indexed for the FTS half of hybrid search |
| `embedding` | `vector(1024)` | Titan v2; NULL until embedded (see backfill) |
| `embedding_model` | TEXT | records which model produced the vector |
| `token_count` | INT | rough (`len/4`) estimate |

**`ingestion_runs`** — audit log: `started_at/finished_at`, `jql`,
`tickets_fetched/upserted`, `chunks_written`, `status` (`running|succeeded|failed`), `error`.

### 6.2 Indexes

- `idx_chunks_embedding_hnsw` — HNSW cosine (`m=16, ef_construction=64`). Tune
  per-session recall with `SET hnsw.ef_search = 100;`.
- `idx_chunks_content_tsv`, `idx_tickets_search_tsv` — GIN full-text.
- `idx_*_trgm` — trigram GIN for fuzzy key/summary matching.
- B-tree on `project`, `status`, `issue_type`, `ticket_id`, `jira_key`.

### 6.3 Functions & views

- **`hybrid_ticket_search(query_embedding, query_text, top_k, vector_weight, fts_weight, project_filter)`**
  — takes top `4×K` vector candidates + top `4×K` FTS candidates, unions them,
  scores `vector*0.7 + fts*0.3`, returns top K with ticket metadata.
  ```sql
  SELECT jira_key, summary, section, combined_score FROM hybrid_ticket_search(
    '[0.1,...]'::vector, 'checkout timeout', 8, 0.7, 0.3, NULL);
  ```
- **`match_ticket_chunks(query_embedding, top_k, project_filter)`** — pure-vector
  fallback (used when the FTS query is empty/stopwords-only).
- **`v_ticket_chunk_stats`** — per-project `tickets / chunks / embedded_chunks`.
  ```sql
  SELECT * FROM v_ticket_chunk_stats;
  SELECT count(*) FROM ticket_chunks WHERE embedding IS NULL;  -- backlog to backfill
  ```

### 6.4 ServiceNow + multi-app (`02_snow_schema.sql`)

**`applications`** — one row per onboarded app (the registry).

| Column | Notes |
|---|---|
| `app_code` (PK) | e.g. `CHECKOUT`, `PAYMENTS` — stamped on every KB row |
| `name`, `description` | human-readable |
| `jira_projects` (TEXT[]) | JIRA project keys feeding this app, e.g. `{CHK}` |
| `snow_groups` (TEXT[]) | ServiceNow assignment groups, e.g. `{CHK-SUPPORT}` |
| `is_active` | soft on/off switch |

**`snow_incidents`** — one row per closed incident (upserted on `number`):
`number` (UNIQUE, e.g. `INC0010234`), `sys_id` (UNIQUE), `app_code` (FK),
`assignment_group`, `category`, `subcategory`, `priority`, `state` (`6`=Resolved,
`7`=Closed), `short_description`, `description`, `close_notes`, `work_notes`,
`caller`, `assigned_to`, `opened_at`, `closed_at`, `snow_url`, `raw_json`,
generated `search_tsv`, row timestamps + update trigger.

**`snow_chunks`** — mirrors `ticket_chunks`: `incident_id` FK (CASCADE),
denormalised `number` + `app_code`, `chunk_index`, `section`
(`short_description` | `description` | `work_notes` | `close_notes`),
`content`, `content_tsv`, `embedding vector(1024)`, HNSW + GIN indexes.

**Functions & views:**

- **`hybrid_snow_search(...)`** / **`match_snow_chunks(...)`** — same contract as
  the JIRA pair, with `p_app_code` + `p_group_filter` instead of `p_project_filter`.
- **`hybrid_kb_search(query_embedding, query_text, top_k, vector_weight, fts_weight, source_filter, app_code, project_filter, group_filter)`**
  — unified cross-source search. Scores are comparable across sources (same
  embedding model, same 0.7/0.3 weights). `source_filter`: `'jira'` | `'snow'` |
  NULL (both). Returns `source, ref_key, grouping, summary, section,
  chunk_index, content, url, vector_score, fts_score, combined_score`.
  ```sql
  SELECT source, ref_key, summary, combined_score FROM hybrid_kb_search(
    '[0.1,...]'::vector, 'vpn outage', 8, 0.7, 0.3, NULL, 'CHECKOUT', NULL, NULL);
  ```
- **`v_kb_stats`** — per-source/group/app `records / chunks / embedded_chunks`.
  ```sql
  SELECT * FROM v_kb_stats;
  SELECT count(*) FROM snow_chunks WHERE embedding IS NULL;
  ```

**Onboarding a new application** (full playbook lives as comments at the bottom
of `02_snow_schema.sql`):

- **Pattern A — shared tables (recommended, zero DDL):**
  ```sql
  INSERT INTO applications (app_code, name, jira_projects, snow_groups)
  VALUES ('CHECKOUT', 'Checkout service', '{CHK}', '{CHK-SUPPORT}');
  ```
  then ingest with the code set (`JIRA_APP_CODE=CHECKOUT ... --source jira`,
  `SNOW_APP_CODE=CHECKOUT ... --source snow`). Retrieval filters on `app_code`.
  An app may fill only one side (JIRA-only or SNOW-only) or both.
- **Pattern B — isolated per-app tables** (only if compliance forces physical
  separation): `CREATE TABLE kb_<app>_... (LIKE ... INCLUDING ALL)`, clone the
  hybrid function with new table names, add the query in `db/retriever.py`.

## 7. Ingestion pipeline

`--source` selects the system (`jira` | `snow` | `all`). Flow per run is the same
for both: **fetch** (pages of 100, up to `--max`) → normalise → **upsert**
→ delete old chunks → **chunk** (section-aware, `CHUNK_SIZE=1200` /
`CHUNK_OVERLAP=200`) → **Bedrock Titan embed** → insert chunks with vectors →
mark `ingestion_runs` (now with a `source` column) succeeded/failed.

- JIRA path: `/search/jql` with `JIRA_JQL_CLOSED`; ADF rich-text → plain text;
  sections `summary → body → resolution`; upsert key `jira_key`.
- ServiceNow path: Table API `incident` with `SNOW_ENCODED_QUERY`
  (default `stateIN6,7^ORDERBYDESCsys_updated_on`); display-value normalisation;
  sections `short_description → description → work_notes → close_notes`
  (close notes = the resolution); upsert key `number`.

```bash
# JIRA only (default — backwards compatible)
python -m jira_agents.ingestion.pipeline --source jira --max 200
# or via installed script:
jira-ingest --source jira --max 200

# ServiceNow only
python -m jira_agents.ingestion.pipeline --source snow --max 200

# Both sources in one run
python -m jira_agents.ingestion.pipeline --source all --max 200

# Store text now, embed later (useful when Bedrock is throttled/offline)
python -m jira_agents.ingestion.pipeline --source all --max 500 --no-embed
python -m jira_agents.ingestion.pipeline --backfill-embeddings   # BOTH chunk tables
```

Behaviour notes:

- **Idempotent** — re-running upserts on `jira_key` / `number` and rewrites that
  record's chunks, so scheduled syncs are safe.
- **JQL** comes from `JIRA_JQL_CLOSED` (default
  `status IN (Closed, Done, Resolved) ORDER BY updated DESC`). Narrow per project
  with e.g. `project = PROJ AND status IN (Closed, Done) ORDER BY updated DESC`.
- **ServiceNow query** comes from `SNOW_ENCODED_QUERY`. Scope per assignment group
  with e.g. `assignment_group=<sys_id>^stateIN6,7^ORDERBYDESCsys_updated_on`.
- **App scoping** — set `JIRA_APP_CODE` / `SNOW_APP_CODE` when ingesting for a
  registered app (see §6.4). Leave empty for unscoped rows. Register the app first:
  `INSERT INTO applications ...`.
- **Progress** prints every 25 records; each run leaves a row in `ingestion_runs`
  (check `source`/`status`/`error` there first when something fails).
- **Backfill** loops `WHERE embedding IS NULL` over **both** chunk tables in
  batches of 32 until none remain.

## 8. Knowledge Agent (RAG)

`agents/knowledge_agent.py` — the only fully implemented agent, now cross-source.

1. **Embed** the question with Titan v2 (`embed_query`).
2. **Retrieve** top-K via `hybrid_kb_search()` (both sources, one ranking); on FTS
   failure (empty query, stopwords) falls back to per-source vector search merged
   in Python. Filters: `source_filter` (`jira`|`snow`|both), `app_code`,
   `project_filter` (JIRA project), `group_filter` (SNOW assignment group);
   `top_k` overrides the default 8.
3. **Prompt** Claude 3.5 Sonnet with a system prompt that enforces:
   answer *only* from context, cite every factual claim as `[KEY]` using the exact
   record key (`[PROJ-123]` for JIRA, `[INC0010234]` for ServiceNow) and name the
   source system when mixing both, say so explicitly when context is insufficient,
   never invent keys/numbers/resolutions.
4. **Respond** with `AgentResponse(answer, citations[{jira_key, summary, section,
   jira_url, score, source}], raw_context)` — citations render from the retrieved
   chunks of either source.

### 8.1 Grounding guarantees — no fabricated solutions (all agents)

Triage, RCA, and Solution share plumbing in `agents/grounded.py` that enforces
KB-only answers structurally, not just by prompt wording:

1. **No chunks → no model call.** If retrieval returns nothing,
   `retrieve_or_insufficient()` returns a "Not enough evidence in the knowledge
   base" verdict (plus what to try next) *without invoking Claude at all* — the
   model never gets a chance to answer from parametric knowledge.
2. **Deterministic decoding.** Every grounded call uses `temperature: 0`.
3. **Strict system preamble** (`GROUNDING_PREAMBLE`, prepended to each agent's
   prompt): use only provided records; every claim/judgement/step must cite its
   `[KEY]`; if evidence is insufficient, say so, list what's missing, and stop —
   never guess, extrapolate, or invent keys/commands/values.

Net effect: the agents can only summarise, compare, and recombine what past
closed records actually say. A fix step that no record supports cannot appear —
the agent must instead report the gap and (for Solution) recommend escalation
with the evidence bundle. All agents are **read-only**: none writes back to
JIRA/ServiceNow; a human approves and applies every plan.

### 8.2 Triage → RCA → Solution (implemented, grounded)

**Triage** (`agents/triage_agent.py`, default top_k=10) — input: new incident
text (+ optional `source_filter`/`app_code`/`project_filter`/`group_filter`).
Outputs fixed sections: Severity (P1–P4) / Priority / Probable owner
(team/component with source system) / Confidence (High/Med/Low) / Evidence /
Immediate next steps / Gaps — each grounded in cited similar records.

**RCA** (`agents/rca_agent.py`, default top_k=10) — input: incident + optional
upstream triage in `extra.triage` (context only, not evidence). Weights
`resolution` / `close_notes` / `work_notes` sections as primary evidence.
Outputs: ranked hypotheses with supporting `[KEY]s` + likelihood, most-likely
pick, tests to confirm (record-sourced only), disprovers, gaps.

**Solution** (`agents/solution_agent.py`, default top_k=8) — input: incident +
optional `extra.triage` / `extra.rca`. Outputs: numbered fix steps (every step
cited, values quoted verbatim), workaround / rollback (or explicit "none in the
KB"), risks, gaps. No supporting record → "Not enough evidence for a fix
recommendation" + escalation guidance.

**Chaining** — pass upstream outputs through `extra` (context, never evidence):
```bash
curl -X POST localhost:8080/invocations -H 'Content-Type: application/json' \
  -d '{"agent":"solution","message":"Checkout returns 500 since 09:40 deploy",
       "app_code":"CHECKOUT",
       "extra":{"triage":"<triage answer>","rca":"<rca answer>"}}'
```

## 9. Running & API reference

### 9.1 CLI

```bash
python -m jira_agents.cli --agent knowledge                        # REPL (both sources)
python -m jira_agents.cli --agent knowledge --message "How was the checkout timeout fixed?"
python -m jira_agents.cli --agent knowledge --message "..." --project PROJ --top-k 5
python -m jira_agents.cli --agent knowledge --source snow --message "How was the VPN outage fixed?"
python -m jira_agents.cli --agent knowledge --source snow --group NET-SUPPORT --message "VPN issues?"
python -m jira_agents.cli --agent knowledge --app CHECKOUT --message "Refund failures?"
python -m jira_agents.cli --agent triage --message "PROD outage: checkout returns 500"
# installed aliases: jira-chat --agent knowledge
```

### 9.2 HTTP gateway

```bash
uvicorn jira_agents.gateway.app:app --host 0.0.0.0 --port 8080
```

| Method & path    | Purpose | Notes |
|---|---|---|
| `GET /ping`      | health check | **Required by AgentCore.** Returns `{"status":"healthy"}`. |
| `GET /agents`    | list agents | Returns `{"agents":["knowledge","rca","solution","triage"]}`. |
| `POST /invocations` | **AgentCore contract** — route + invoke | Body = `InvokePayload` (below). |
| `POST /chat`     | alias of `/invocations` | friendlier name for local dev / curl. |
| `GET /docs`      | Swagger UI | auto-generated. |

**Request (`InvokePayload`)** — all fields optional except `message`:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `agent` | string | `"knowledge"` | `knowledge` \| `triage` \| `rca` \| `solution`. Unknown → 400. |
| `message` | string (req) | — | user question / incident description |
| `session_id` | string \| null | null | conversation id (accepted, stateless in v1) |
| `source_filter` | string \| null | null | `'jira'` \| `'snow'` \| null (both). Anything else → 400. |
| `project_filter` | string \| null | null | scope retrieval to one JIRA project |
| `group_filter` | string \| null | null | scope retrieval to one ServiceNow assignment group |
| `app_code` | string \| null | null | scope retrieval to one onboarded app (`applications.app_code`) |
| `top_k` | int \| null | null | override retrieval depth (default from `TOP_K`) |
| `extra` | object | `{}` | channel for chaining agents (e.g. triage output → solution) |

**Response:**

```json
{
  "agent": "knowledge",
  "answer": "The checkout timeout was fixed by ... [PROJ-123]",
  "citations": [{"jira_key": "PROJ-123", "summary": "...", "section": "resolution",
                 "jira_url": "https://.../browse/PROJ-123", "score": 0.87, "source": "jira"}],
  "context": [{"source": "jira", "jira_key": "PROJ-123", "section": "resolution",
               "score": 0.87, "content": "..."}]
}
```
(`jira_key` carries the record key for both sources — a JIRA key or a ServiceNow
`INC` number; `source` tells them apart.)

**Examples:**

```bash
curl -X POST localhost:8080/chat -H 'Content-Type: application/json' \
  -d '{"agent":"knowledge","message":"How was PROJ-123 fixed?"}'

curl -X POST localhost:8080/invocations -H 'Content-Type: application/json' \
  -d '{"agent":"knowledge","message":"Refund failures in payments","project_filter":"PAY","top_k":5}'

curl -X POST localhost:8080/invocations -H 'Content-Type: application/json' \
  -d '{"agent":"knowledge","message":"VPN outage fix?","source_filter":"snow"}'

curl -X POST localhost:8080/invocations -H 'Content-Type: application/json' \
  -d '{"agent":"knowledge","message":"Checkout timeouts","app_code":"CHECKOUT","top_k":10}'

curl -X POST localhost:8080/invocations -H 'Content-Type: application/json' \
  -d '{"agent":"triage","message":"PROD outage: checkout returns 500","app_code":"CHECKOUT"}'

curl -X POST localhost:8080/invocations -H 'Content-Type: application/json' \
  -d '{"agent":"rca","message":"Checkout 500s after deploy, pool timeouts in logs",
       "app_code":"CHECKOUT","extra":{"triage":"<paste triage answer>"}}'
# See §8.2 for the full triage → rca → solution chaining pattern.
```

## 10. Configuration reference

Everything comes from `.env` (copy `.env.example`) or real environment variables
(which take precedence — this is how AgentCore Runtime injects secrets).
`DATABASE_URL` (uppercase) is accepted as an alias for `database_url`.

| Variable | Default | Used by |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/jirakb` | DB pool, pipeline, retriever |
| `AWS_REGION` | `us-east-1` | Bedrock calls, AgentCore deploy |
| `EMBEDDING_MODEL_ID` | `amazon.titan-embed-text-v2` | pipeline, retriever query embedding |
| `EMBEDDING_DIM` | `1024` | must match `vector(N)` in SQL |
| `CHAT_MODEL_ID` | `anthropic.claude-3-5-sonnet-20241022-v2:0` | Knowledge Agent |
| `JIRA_BASE_URL` | `https://your-domain.atlassian.net` | pipeline |
| `JIRA_EMAIL` / `JIRA_API_TOKEN` | empty | pipeline (Basic auth) |
| `JIRA_JQL_CLOSED` | `status IN (Closed, Done, Resolved) ORDER BY updated DESC` | pipeline |
| `JIRA_MAX_TICKETS` | `500` | pipeline default cap |
| `JIRA_APP_CODE` | empty (unscoped) | app code stamped on JIRA rows |
| `SNOW_INSTANCE_URL` | `https://your-instance.service-now.com` | pipeline |
| `SNOW_USERNAME` / `SNOW_PASSWORD` | empty | pipeline (Basic auth) |
| `SNOW_ENCODED_QUERY` | `stateIN6,7^ORDERBYDESCsys_updated_on` | pipeline |
| `SNOW_MAX_INCIDENTS` | `500` | pipeline default cap |
| `SNOW_APP_CODE` | empty (unscoped) | app code stamped on ServiceNow rows |
| `TOP_K` | `8` | retrieval default |
| `VECTOR_WEIGHT` / `FTS_WEIGHT` | `0.7` / `0.3` | hybrid scoring |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1200` / `200` | chunker |
| `GATEWAY_PORT` | `8080` | Dockerfile CMD, local uvicorn |

## 11. Deploying to AWS AgentCore

### 11.1 What you need first

- AWS CLI configured (`aws sts get-caller-identity` works).
- An **AgentCore execution role** ARN with: Bedrock `InvokeModel` (Titan + Claude),
  ECR push/pull, CloudWatch Logs write, and access to the DB secret / network path
  to Aurora. Export it: `export AGENTCORE_ROLE_ARN=arn:aws:iam::<acct>:role/<name>`.
- `DATABASE_URL` pointing at **Aurora** (not localhost) — inject via the Runtime's
  environment config / Secrets Manager in production. Never bake credentials into the image.

### 11.2 One-command deploy

```bash
export AGENTCORE_ROLE_ARN=arn:aws:iam::<acct>:role/AgentCoreExecutionRole
./deploy/deploy_agentcore.sh jira-knowledge-agent us-east-1
```

Step by step, the script: creates the ECR repo if missing → docker-logins to ECR →
`docker build` (see `Dockerfile`: `python:3.12-slim` + `libpq5`, `pip install -e
".[agentcore]"`, serves `uvicorn jira_agents.gateway.app:app` on `$GATEWAY_PORT`)
→ tags + pushes `:latest` → runs `deploy/deploy_agentcore.py`, which calls
`bedrock-agentcore-control CreateAgentRuntime` with the image URI + role ARN
(public network mode).

### 11.3 Invoke the deployed runtime

```bash
aws bedrock-agentcore invoke-agent-runtime \
  --agent-runtime-id <RUNTIME_ID_FROM_CREATE_OUTPUT> \
  --payload '{"agent":"knowledge","message":"How was PROJ-123 fixed?"}' \
  --region us-east-1 out.json && cat out.json
```

Redeploys are the same command (rebuilds + pushes a new `:latest`, then creates a
new runtime revision). Pointing the container at a new Aurora endpoint is an env
change — no code change needed.

## 12. Extending Triage → RCA → Solution

The three agents are implemented and grounded (§8.1–8.2). To extend them:

- **Tune retrieval depth** per agent via `default_top_k` in each file, or per call
  with `top_k`.
- **Weight resolution evidence** — RCA/Solution prompts already prioritise
  `resolution` / `close_notes` sections; to enforce it deterministically, add a
  section preference in `retriever.py` (e.g. score boost for those sections).
- **Write-back (optional, off by default)** — add a JIRA/ServiceNow update call
  behind an explicit human-approval flag; keep these agents read-only until then.
- **Chaining** stays via `req.extra` (`triage` → `rca` → `solution`); upstream
  text is labelled context-only so it can never substitute for cited evidence.

## 13. Adding a new agent

1. Create `src/jira_agents/agents/<name>_agent.py` implementing the contract:
   ```python
   from .base import AgentRequest, AgentResponse

   class MyAgent:
       name = "myagent"
       def invoke(self, req: AgentRequest) -> AgentResponse:
           return AgentResponse(answer="...", agent=self.name)
   ```
2. Register one line in `agents/registry.py`:
   ```python
   from .my_agent import MyAgent
   AGENT_REGISTRY["myagent"] = MyAgent()
   ```
   (or at runtime: `register_agent("myagent", MyAgent())`).
3. No gateway change — `POST /invocations {"agent":"myagent", ...}` routes
   automatically; unknown names return 400 with the valid list. Verify with
   `GET /agents` and `python -m jira_agents.cli --agent myagent`.

## 14. Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| `extension "vector" does not exist` | pgvector not installed / not enabled → use `pgvector/pgvector:pg16` locally; on Aurora enable the extension, then re-run `sql/01_schema.sql`. |
| `invalid input dimensions ... expected 1024` | embedding model ≠ column width → set `EMBEDDING_MODEL_ID` back to Titan v2, or `ALTER TABLE ticket_chunks/snow_chunks ALTER COLUMN embedding TYPE vector(<N>)` + drop HNSW index + re-embed everything. |
| `401/403 from JIRA` | wrong email/token or URL → verify `JIRA_BASE_URL/EMAIL/API_TOKEN`; token from id.atlassian.com. |
| `401/403 from ServiceNow` | wrong instance/user/password or missing `incident` read role → verify `SNOW_*`; test `GET /api/now/table/incident?sysparm_limit=1` manually. |
| `relation "snow_incidents" does not exist` | ran `01` but not `02` → `psql "$DATABASE_URL" -f sql/02_snow_schema.sql`. |
| `AccessDeniedException` Bedrock | model access not granted → enable the Titan + Claude models in Bedrock console (region must match `AWS_REGION`). |
| Empty answers, `embedded_chunks=0` | ingested with `--no-embed` → run `--backfill-embeddings` (covers both chunk tables); check `SELECT * FROM v_kb_stats`. |
| `hybrid_*_search` FTS errors on odd input | stopwords-only query → code already falls back to pure vector search; no action. |
| AgentCore `/ping` unhealthy | container not listening on `$GATEWAY_PORT` → check env `GATEWAY_PORT` and security groups / VPC reachability to Aurora. |
| `Unknown agent` 400 | typo in `agent` field → `GET /agents` for the valid list. |
| `source_filter` 400 | must be `'jira'`, `'snow'`, or null → both sources searched when null. |
| Answers ignore an app's data | rows ingested without `app_code`, or wrong code → check `SELECT DISTINCT app_code FROM ticket_chunks UNION ...`; re-ingest with `JIRA_APP_CODE`/`SNOW_APP_CODE` set. |

## 15. Security notes

- `.env` holds secrets and is git-ignored — never commit it, never bake it into
  the Docker image. In AgentCore, inject `DATABASE_URL`/`JIRA_API_TOKEN`/
  `SNOW_PASSWORD` via the Runtime environment or Secrets Manager.
- Prefer least-privilege credentials: JIRA token read-only, ServiceNow integration
  user with read on `incident` (+ journals) only, until write-back agents land.
  Aurora user limited to the app schema; use RDS IAM auth or Secrets Manager
  rotation in production.
- The `/invocations` API is unauthenticated by default — front it with API Gateway
  / IAM / Cognito or AgentCore's auth before exposing beyond your VPC.
=======

