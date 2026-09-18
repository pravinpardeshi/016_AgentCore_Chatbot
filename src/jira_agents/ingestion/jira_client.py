"""Minimal JIRA Cloud REST client for closed-ticket ingestion.

Uses `/rest/api/3/search/jql` (paginated). Auth = email + API token (basic auth).
Set JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN in `.env`.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

import requests


@dataclass
class JiraTicket:
    key: str
    project: str
    issue_type: str | None
    status: str | None
    priority: str | None
    summary: str
    description: str | None
    resolution: str | None
    assignee: str | None
    reporter: str | None
    labels: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    created: str | None = None
    updated: str | None = None
    resolved: str | None = None
    url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _adf_to_text(node: Any) -> str:
    """Convert JIRA ADF (rich text JSON) to plain text (best-effort)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if "text" in node and "type" not in node:
            return str(node["text"])
        parts: list[str] = []
        if node.get("type") == "text":
            parts.append(node.get("text", ""))
        for child in node.get("content", []) or []:
            parts.append(_adf_to_text(child))
        # hardBreak / rule -> newline
        if node.get("type") in ("hardBreak", "rule"):
            parts.append("\n")
        return "".join(parts) if len(parts) > 1 else " ".join(parts) if parts else ""
    if isinstance(node, list):
        texts = [_adf_to_text(n) for n in node]
        return "\n".join(t for t in texts if t)
    return ""


def _clean(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return _adf_to_text(v) or None


class JiraClient:
    def __init__(self, base_url: str, email: str, api_token: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Basic {token}", "Accept": "application/json"}
        )
        self.timeout = timeout

    def search_closed(self, jql: str, max_results: int = 500) -> list[JiraTicket]:
        """Paginate through search/jql endpoint. Returns normalised tickets."""
        out: list[JiraTicket] = []
        next_token: str | None = None
        fields = (
            "summary,description,issuetype,status,priority,assignee,reporter,"
            "labels,components,created,updated,resolutiondate,resolution"
        )
        while len(out) < max_results:
            payload: dict[str, Any] = {
                "jql": jql,
                "maxResults": min(100, max_results - len(out)),
                "fields": fields.split(","),
            }
            if next_token:
                payload["nextPageToken"] = next_token
            resp = self.session.post(
                f"{self.base_url}/rest/api/3/search/jql",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            for issue in data.get("issues", []):
                out.append(self._normalise(issue))
                if len(out) >= max_results:
                    break
            next_token = data.get("nextPageToken")
            if not next_token or not data.get("issues"):
                break
        return out

    def _normalise(self, issue: dict[str, Any]) -> JiraTicket:
        f = issue.get("fields", {}) or {}
        key: str = issue.get("key", "")
        proj = (f.get("project") or {}).get("key", key.split("-")[0] if "-" in key else "?")

        def _name(d: Any) -> str | None:
            return (d or {}).get("displayName") if isinstance(d, dict) else None

        return JiraTicket(
            key=key,
            project=proj,
            issue_type=(f.get("issuetype") or {}).get("name"),
            status=(f.get("status") or {}).get("name"),
            priority=(f.get("priority") or {}).get("name"),
            summary=f.get("summary") or "",
            description=_clean(f.get("description")),
            resolution=(f.get("resolution") or {}).get("name")
            or _clean(f.get("resolution")),
            assignee=_name(f.get("assignee")),
            reporter=_name(f.get("reporter")),
            labels=list(f.get("labels") or []),
            components=[c.get("name") for c in (f.get("components") or []) if c.get("name")],
            created=f.get("created"),
            updated=f.get("updated"),
            resolved=f.get("resolutiondate"),
            url=f"{self.base_url}/browse/{key}",
            raw=issue,
        )
