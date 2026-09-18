"""Central settings — every agent + pipeline + gateway reads from here.

Copy `.env.example` to `.env` and fill in values. All settings can also be
passed as environment variables (e.g. in AgentCore Runtime config).
"""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- AWS / Bedrock ---
    aws_region: str = Field(default="us-east-1", description="Bedrock + AgentCore region")
    embedding_model_id: str = Field(default="amazon.titan-embed-text-v2")
    embedding_dim: int = Field(default=1024)
    chat_model_id: str = Field(
        default="anthropic.claude-3-5-sonnet-20241022-v2:0",
        description="Bedrock chat model for Knowledge Agent",
    )

    # --- Postgres / Aurora pgvector ---
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/jirakb",
        description="SQLAlchemy/psycopg URL for Aurora Postgres with pgvector",
    )

    # --- JIRA ---
    jira_base_url: str = Field(default="https://your-domain.atlassian.net")
    jira_email: str = Field(default="")
    jira_api_token: str = Field(default="")
    jira_jql_closed: str = Field(
        default='status IN (Closed, Done, Resolved) ORDER BY updated DESC'
    )
    jira_max_tickets: int = Field(default=500)
    jira_app_code: str = Field(
        default="",
        description="Application code stamped on JIRA rows (see applications table). Empty = unscoped.",
    )

    # --- ServiceNow ---
    snow_instance_url: str = Field(default="https://your-instance.service-now.com")
    snow_username: str = Field(default="")
    snow_password: str = Field(default="")
    snow_encoded_query: str = Field(
        default="stateIN6,7^ORDERBYDESCsys_updated_on",
        description="Encoded query for closed incidents (6=Resolved, 7=Closed)",
    )
    snow_max_incidents: int = Field(default=500)
    snow_app_code: str = Field(
        default="",
        description="Application code stamped on ServiceNow rows. Empty = unscoped.",
    )

    # --- Retrieval defaults ---
    top_k: int = Field(default=8)
    vector_weight: float = Field(default=0.7)
    fts_weight: float = Field(default=0.3)

    # --- Chunking ---
    chunk_size: int = Field(default=1200)
    chunk_overlap: int = Field(default=200)

    # --- Gateway ---
    gateway_host: str = Field(default="0.0.0.0")
    gateway_port: int = Field(default=8080)


@lru_cache
def get_settings() -> Settings:
    # Allow DATABASE_URL (common convention) as alias for database_url
    if "DATABASE_URL" in os.environ and "database_url" not in os.environ:
        os.environ["database_url"] = os.environ["DATABASE_URL"]
    return Settings()
