FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# psycopg (binary) needs libpq; slim image: install runtime lib only
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY sql/ ./sql/
RUN pip install --upgrade pip && pip install -e ".[agentcore]"

ENV GATEWAY_PORT=8080
EXPOSE 8080

# AgentCore Runtime hits GET /ping and POST /invocations
CMD ["sh", "-c", "uvicorn jira_agents.gateway.app:app --host 0.0.0.0 --port ${GATEWAY_PORT:-8080}"]
