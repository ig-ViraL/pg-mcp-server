"""Append-only audit log.

One JSON object per line, written for every tool call that touches the
database. Result rows are never logged. Connection strings are reduced to
host/database with credentials removed.

Enable with PGMCP_AUDIT_LOG=/path/to/audit.jsonl (or "-" for stderr), or
[audit] path = "..." in the config file.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from pg_mcp_server.config import get_policy
from pg_mcp_server.db.pool import _db_url_var, _session_id_var

_lock = threading.Lock()


def describe_db(url: str | None) -> str | None:
    """Return host:port/dbname for a connection URL, never the credentials."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        return f"{host}{port}{parts.path}"
    except Exception:
        return None


def _actor() -> str | None:
    sid = _session_id_var.get()
    if not sid:
        return None
    return "session:" + hashlib.sha256(sid.encode()).hexdigest()[:12]


def record(
    tool: str,
    decision: str,
    *,
    sql: str | None = None,
    fingerprint: str | None = None,
    reason: str | None = None,
    rows: int | None = None,
    truncated: bool | None = None,
    tables: list[str] | None = None,
    started: float | None = None,
) -> dict | None:
    """Write one audit event. decision is 'allowed', 'blocked' or 'error'."""
    policy = get_policy()
    if not policy.audit_path:
        return None

    event: dict = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "tool": tool,
        "decision": decision,
        "db": describe_db(_db_url_var.get() or os.environ.get("DATABASE_URL")),
        "actor": _actor(),
    }
    if fingerprint:
        event["fingerprint"] = fingerprint
    if sql is not None and policy.audit_include_sql:
        event["sql"] = sql
    if tables:
        event["tables"] = tables
    if rows is not None:
        event["rows"] = rows
    if truncated:
        event["truncated"] = True
    if reason:
        event["reason"] = reason
    if started is not None:
        event["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)

    line = json.dumps(event, default=str)
    with _lock:
        if policy.audit_path == "-":
            print(line, file=sys.stderr, flush=True)
        else:
            path = os.path.expanduser(policy.audit_path)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    return event
