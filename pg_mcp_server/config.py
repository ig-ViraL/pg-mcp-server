"""Runtime policy configuration.

Settings come from (lowest to highest precedence):
  1. built-in defaults
  2. a TOML file given by --config or PGMCP_CONFIG
  3. PGMCP_* environment variables

Example pg-mcp.toml:

    [policy]
    allow_tables = ["orders", "customers", "products"]
    deny_tables = ["admin_users"]
    deny_columns = ["customers.email", "customers.phone"]
    max_rows = 500
    statement_timeout_ms = 5000
    allow_annotations = true
    allow_explain_analyze = false

    [audit]
    path = "./pg-mcp-audit.jsonl"   # "-" for stderr
    include_sql = true
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Catalogs that expose credentials or server files. Always denied.
ALWAYS_DENIED_TABLES = frozenset({
    "pg_authid", "pg_shadow", "pg_user_mapping", "pg_user_mappings",
    "pg_hba_file_rules", "pg_ident_file_mappings", "pg_file_settings",
    "pg_largeobject", "pg_statistic",
})


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Policy:
    allow_tables: frozenset[str] = frozenset()      # empty = every table allowed
    deny_tables: frozenset[str] = frozenset()
    deny_columns: frozenset[tuple[str, str]] = frozenset()  # (table, column)
    max_rows: int = 500
    default_rows: int = 100
    statement_timeout_ms: int = 5000
    allow_annotations: bool = True
    allow_explain_analyze: bool = False
    audit_path: str | None = None
    audit_include_sql: bool = True

    # ── helpers ──────────────────────────────────────────────────────────────

    def table_allowed(self, table: str) -> bool:
        t = table.lower()
        if t in ALWAYS_DENIED_TABLES or t in self.deny_tables:
            return False
        if self.allow_tables and t not in self.allow_tables:
            return False
        return True

    def denied_columns_for(self, table: str) -> set[str]:
        t = table.lower()
        return {c for (tbl, c) in self.deny_columns if tbl == t}

    def clamp_rows(self, limit: int | None) -> int:
        if limit is None or limit <= 0:
            return self.default_rows
        return min(limit, self.max_rows)


@dataclass
class _State:
    policy: Policy = field(default_factory=Policy)


_state = _State()


def get_policy() -> Policy:
    return _state.policy


def set_policy(policy: Policy) -> None:
    _state.policy = policy


def _parse_columns(items: list[str]) -> frozenset[tuple[str, str]]:
    out = set()
    for item in items:
        if "." not in item:
            raise ValueError(f"deny_columns entry must be 'table.column', got {item!r}")
        table, col = item.rsplit(".", 1)
        out.add((table.split(".")[-1].lower(), col.lower()))
    return frozenset(out)


def load_policy(config_path: str | None = None, env: dict[str, str] | None = None) -> Policy:
    env = dict(os.environ if env is None else env)
    data: dict = {}
    path = config_path or env.get("PGMCP_CONFIG")
    if path:
        with Path(path).expanduser().open("rb") as fh:
            data = tomllib.load(fh)

    pol = data.get("policy", {})
    aud = data.get("audit", {})

    allow = list(pol.get("allow_tables", []))
    deny = list(pol.get("deny_tables", []))
    deny_cols = list(pol.get("deny_columns", []))
    max_rows = int(pol.get("max_rows", 500))
    timeout = int(pol.get("statement_timeout_ms", 5000))
    allow_ann = bool(pol.get("allow_annotations", True))
    allow_analyze = bool(pol.get("allow_explain_analyze", False))
    audit_path = aud.get("path")
    include_sql = bool(aud.get("include_sql", True))

    if "PGMCP_ALLOW_TABLES" in env:
        allow = _split(env["PGMCP_ALLOW_TABLES"])
    if "PGMCP_DENY_TABLES" in env:
        deny = _split(env["PGMCP_DENY_TABLES"])
    if "PGMCP_DENY_COLUMNS" in env:
        deny_cols = _split(env["PGMCP_DENY_COLUMNS"])
    if "PGMCP_MAX_ROWS" in env:
        max_rows = int(env["PGMCP_MAX_ROWS"])
    if "PGMCP_STATEMENT_TIMEOUT_MS" in env:
        timeout = int(env["PGMCP_STATEMENT_TIMEOUT_MS"])
    if "PGMCP_ALLOW_ANNOTATIONS" in env:
        allow_ann = _bool(env["PGMCP_ALLOW_ANNOTATIONS"])
    if "PGMCP_ALLOW_EXPLAIN_ANALYZE" in env:
        allow_analyze = _bool(env["PGMCP_ALLOW_EXPLAIN_ANALYZE"])
    if "PGMCP_AUDIT_LOG" in env:
        audit_path = env["PGMCP_AUDIT_LOG"] or None
    if "PGMCP_AUDIT_INCLUDE_SQL" in env:
        include_sql = _bool(env["PGMCP_AUDIT_INCLUDE_SQL"])

    if max_rows < 1:
        raise ValueError("max_rows must be at least 1")

    return Policy(
        allow_tables=frozenset(t.split(".")[-1].lower() for t in allow),
        deny_tables=frozenset(t.split(".")[-1].lower() for t in deny),
        deny_columns=_parse_columns(deny_cols),
        max_rows=max_rows,
        default_rows=min(100, max_rows),
        statement_timeout_ms=timeout,
        allow_annotations=allow_ann,
        allow_explain_analyze=allow_analyze,
        audit_path=audit_path,
        audit_include_sql=include_sql,
    )
