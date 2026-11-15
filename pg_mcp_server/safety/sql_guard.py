"""Parser-based SQL guard for PostgreSQL.

Every query an agent sends is parsed with libpg_query (via pglast), the same
parser PostgreSQL itself uses. A query is allowed only when:

  * it is exactly one statement, and that statement is a plain SELECT
    (WITH ... SELECT and VALUES are SELECTs too);
  * no part of the tree is a data-modifying statement (e.g. a DELETE inside
    a CTE), SELECT INTO, or a row-locking clause (FOR UPDATE / FOR SHARE);
  * it calls no function on the deny list (file access, large objects,
    dblink, backend signalling, config changes, sequences, sleeps, ...);
  * every table it reads passes the configured allow/deny policy;
  * it does not read any column on the deny list (best effort; use column
    GRANTs in the database for a hard guarantee).

This replaces keyword regexes, which can be bypassed with quoting, comments
or unusual whitespace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator

from pglast.parser import ParseError, fingerprint, parse_sql_json

from pg_mcp_server.config import Policy, get_policy

# Exact function names that must never run from an agent query.
DENIED_FUNCTIONS = frozenset({
    "pg_read_file", "pg_read_binary_file", "pg_stat_file", "pg_ls_dir",
    "pg_ls_logdir", "pg_ls_waldir", "pg_ls_tmpdir", "pg_ls_archive_statusdir",
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_rotate_logfile", "pg_switch_wal", "pg_create_restore_point",
    "pg_promote", "pg_log_backend_memory_contexts",
    "set_config", "nextval", "setval", "pg_notify",
    "query_to_xml", "query_to_xml_and_xmlschema", "query_to_xmlschema",
    "cursor_to_xml", "cursor_to_xmlschema",
    "table_to_xml", "table_to_xml_and_xmlschema", "table_to_xmlschema",
    "schema_to_xml", "schema_to_xml_and_xmlschema", "schema_to_xmlschema",
    "database_to_xml", "database_to_xml_and_xmlschema", "database_to_xmlschema",
    "pg_import_system_collations", "pg_file_write", "pg_file_rename",
    "pg_file_unlink", "pg_logdir_ls", "pg_current_logfile",
})

# Function name prefixes that are denied as a family.
DENIED_PREFIXES = (
    "lo_",               # large objects: lo_import / lo_export read & write server files
    "dblink",            # outbound connections to other databases
    "pg_advisory",       # advisory locks
    "pg_try_advisory",
    "pg_replication_",
    "pg_logical_",
    "pg_create_",
    "pg_drop_",
    "pg_copy_",
    "pg_stat_reset",
    "pg_wal_replay",
    "pg_file_",
)


@dataclass
class GuardResult:
    ok: bool
    reason: str | None = None
    tables: list[str] = field(default_factory=list)
    fingerprint: str | None = None


def _walk(node: Any, parent_key: str | None = None) -> Iterator[tuple[str | None, Any]]:
    """Yield (key, value) for every dict entry in the parse tree, depth first."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from _walk(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, parent_key)


def _string_names(fields: list) -> list[str]:
    out = []
    for f in fields or []:
        if isinstance(f, dict) and "String" in f:
            out.append(f["String"].get("sval", "").lower())
    return out


def _function_denied(name: str) -> bool:
    return name in DENIED_FUNCTIONS or name.startswith(DENIED_PREFIXES)


def safe_fingerprint(sql: str) -> str | None:
    try:
        return fingerprint(sql)
    except Exception:
        return None


def check_sql(sql: str, policy: Policy | None = None) -> GuardResult:
    policy = policy or get_policy()

    if not sql or not sql.strip():
        return GuardResult(False, "Query is empty")

    try:
        tree = json.loads(parse_sql_json(sql))
    except ParseError as e:
        return GuardResult(False, f"Could not parse SQL: {e}")

    stmts = tree.get("stmts", [])
    if len(stmts) != 1:
        return GuardResult(False, "Exactly one statement is allowed per query")

    top = stmts[0].get("stmt", {})
    if list(top.keys()) != ["SelectStmt"]:
        kind = next(iter(top.keys()), "unknown")
        return GuardResult(False, f"Only SELECT queries are allowed (got {kind})")

    fp = safe_fingerprint(sql)

    cte_names: set[str] = set()
    range_vars: list[dict] = []
    column_refs: list[list] = []

    for key, value in _walk(top):
        if key and key.endswith("Stmt") and key != "SelectStmt":
            return GuardResult(False, f"Data-modifying or non-SELECT statement inside query ({key})", fingerprint=fp)
        if key == "intoClause":
            return GuardResult(False, "SELECT INTO is not allowed", fingerprint=fp)
        if key == "lockingClause":
            return GuardResult(False, "Row locking (FOR UPDATE / FOR SHARE) is not allowed", fingerprint=fp)
        if key == "FuncCall":
            names = _string_names(value.get("funcname", []))
            if names and _function_denied(names[-1]):
                return GuardResult(False, f"Function {names[-1]}() is not allowed", fingerprint=fp)
        elif key == "CommonTableExpr":
            cte_names.add(str(value.get("ctename", "")).lower())
        elif key == "RangeVar" or (key in ("relation",) and isinstance(value, dict) and "relname" in value):
            range_vars.append(value)
        elif key == "ColumnRef":
            column_refs.append(value.get("fields", []))

    # Resolve real tables (CTE names without a schema are not tables).
    tables: list[str] = []
    aliases: dict[str, str] = {}
    for rv in range_vars:
        rel = str(rv.get("relname", "")).lower()
        schema = rv.get("schemaname")
        if not schema and rel in cte_names:
            continue
        if rel not in tables:
            tables.append(rel)
        alias = (rv.get("alias") or {}).get("aliasname")
        if alias:
            aliases[alias.lower()] = rel
        aliases.setdefault(rel, rel)

    for t in tables:
        if not policy.table_allowed(t):
            return GuardResult(False, f"Access to table '{t}' is not allowed by policy", tables, fp)

    restricted = {t: policy.denied_columns_for(t) for t in tables}
    restricted = {t: cols for t, cols in restricted.items() if cols}
    if restricted:
        all_denied = set().union(*restricted.values())
        for fields in column_refs:
            names = _string_names(fields)
            has_star = any(isinstance(f, dict) and "A_Star" in f for f in fields)
            if has_star:
                qual = names[-1] if names else None
                if qual is None or aliases.get(qual) in restricted:
                    return GuardResult(
                        False,
                        "SELECT * is not allowed on tables with restricted columns; list the columns you need",
                        tables, fp,
                    )
                continue
            if not names:
                continue
            col = names[-1]
            if len(names) >= 2:
                table = aliases.get(names[-2], names[-2])
                if col in restricted.get(table, set()):
                    return GuardResult(False, f"Column '{table}.{col}' is restricted by policy", tables, fp)
            elif col in all_denied:
                return GuardResult(False, f"Column '{col}' is restricted by policy", tables, fp)
            elif aliases.get(col) in restricted:
                # A bare table or alias reference (e.g. row_to_json(c)) reads the whole row.
                return GuardResult(False, f"Whole-row reference to '{col}' is not allowed on tables with restricted columns", tables, fp)

    return GuardResult(True, None, tables, fp)
