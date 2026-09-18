"""ServiceNow Table API client for closed-incident ingestion.

Reads from the `incident` table via `/api/now/table/incident`.
Auth = basic (username + password). For production, prefer an OAuth token or
a least-privilege integration user with read access to `incident` + journals.

Encoded query default pulls Resolved (6) + Closed (7), newest first:
    stateIN6,7^ORDERBYDESCsys_updated_on
Scope to an assignment group per app, e.g.:
    assignment_group=javascript:...  → use sys_id, or filter client-side.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

FIELDS = (
    "number,sys_id,short_description,description,close_notes,work_notes,"
    "assignment_group,category,subcategory,priority,state,"
    "caller_id,assigned_to,opened_at,closed_at,sys_updated_on"
)


@dataclass
class SnowIncident:
    number: str
    sys_id: str
    short_description: str
    description: str | None
    close_notes: str | None
    work_notes: str | None
    assignment_group: str | None
    category: str | None
    subcategory: str | None
    priority: str | None
    state: str | None
    caller: str | None
    assigned_to: str | None
    opened: str | None
    closed: str | None
    url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _dv(value: Any) -> str | None:
    """Extract display value (Table API may return {value, display_value} or plain str)."""
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return str(value)


class ServiceNowClient:
    def __init__(self, instance_url: str, username: str, password: str, timeout: int = 30):
        self.instance_url = instance_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({"Accept": "application/json"})
        self.timeout = timeout

    def search_closed(self, encoded_query: str, max_results: int = 500) -> list[SnowIncident]:
        """Paginate with sysparm_offset/limit. Returns normalised incidents."""
        out: list[SnowIncident] = []
        offset = 0
        while len(out) < max_results:
            limit = min(100, max_results - len(out))
            resp = self.session.get(
                f"{self.instance_url}/api/now/table/incident",
                params={
                    "sysparm_query": encoded_query,
                    "sysparm_fields": FIELDS,
                    "sysparm_limit": limit,
                    "sysparm_offset": offset,
                    "sysparm_display_value": "true",
                    "sysparm_exclude_reference_link": "true",
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            rows = resp.json().get("result", [])
            if not rows:
                break
            for r in rows:
                out.append(self._normalise(r))
                if len(out) >= max_results:
                    break
            if len(rows) < limit:
                break
            offset += len(rows)
        return out

    def _normalise(self, r: dict[str, Any]) -> SnowIncident:
        number = r.get("number", "")
        return SnowIncident(
            number=number,
            sys_id=r.get("sys_id", ""),
            short_description=r.get("short_description") or "",
            description=r.get("description") or None,
            close_notes=r.get("close_notes") or None,
            work_notes=(r.get("work_notes") or None),
            assignment_group=_dv(r.get("assignment_group")),
            category=_dv(r.get("category")),
            subcategory=_dv(r.get("subcategory")),
            priority=_dv(r.get("priority")),
            state=_dv(r.get("state")),
            caller=_dv(r.get("caller_id")),
            assigned_to=_dv(r.get("assigned_to")),
            opened=r.get("opened_at"),
            closed=r.get("closed_at"),
            url=f"{self.instance_url}/nav_to.do?uri=incident.do?sysparm_query=number={number}",
            raw=r,
        )
