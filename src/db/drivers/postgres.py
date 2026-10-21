import json
import asyncpg
from .base import BaseDriver


class PostgresDriver(BaseDriver):
    """PostgreSQL driver using asyncpg."""

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def connect(self, url: str) -> None:
        self._pool = await asyncpg.create_pool(
            url, min_size=1, max_size=5, command_timeout=10
        )

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    # ── run_query ─────────────────────────────────────────────────────────────

    async def run_query(self, sql: str, limit: int = 100) -> dict:
        ok, reason = self.validate_query(sql)
        if not ok:
            return {"isError": True, "content": reason}
        sql = self.inject_limit(sql, limit)
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SET statement_timeout = '5000'")
                # Read-only transaction: even a query that passes the validator cannot write.
                async with conn.transaction(readonly=True):
                    rows = await conn.fetch(sql)
            return {
                "isError": False,
                "content": json.dumps(
                    {"rows": [dict(r) for r in rows], "row_count": len(rows)},
                    default=str,
                ),
            }
        except asyncpg.exceptions.QueryCanceledError:
            return {"isError": True, "content": "Query timed out after 5 seconds"}
        except asyncpg.PostgresError as e:
            return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}

    # ── list_tables ───────────────────────────────────────────────────────────

    _COLS_SQL = """
WITH pk_cols AS (
    SELECT tc.table_name, kcu.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
    WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = 'public'
),
fk_cols AS (
    SELECT kcu.table_name, kcu.column_name, ccu.table_name AS ref_table, ccu.column_name AS ref_column
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage ccu
        ON ccu.constraint_name = tc.constraint_name
    WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
),
col_comments AS (
    SELECT pgc.relname AS table_name, a.attname AS column_name, d.description
    FROM pg_catalog.pg_class pgc
    JOIN pg_catalog.pg_attribute a ON a.attrelid = pgc.oid AND a.attnum > 0 AND NOT a.attisdropped
    LEFT JOIN pg_catalog.pg_description d ON d.objoid = pgc.oid AND d.objsubid = a.attnum
    WHERE pgc.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
)
SELECT
    c.table_name, c.column_name, c.data_type, c.is_nullable, c.column_default,
    (pk.column_name IS NOT NULL) AS is_primary_key,
    fk.ref_table AS fk_table, fk.ref_column AS fk_column,
    cc.description AS column_comment
FROM information_schema.columns c
LEFT JOIN pk_cols pk ON pk.table_name = c.table_name AND pk.column_name = c.column_name
LEFT JOIN fk_cols fk ON fk.table_name = c.table_name AND fk.column_name = c.column_name
LEFT JOIN col_comments cc ON cc.table_name = c.table_name AND cc.column_name = c.column_name
WHERE c.table_schema = 'public'
ORDER BY c.table_name, c.ordinal_position
"""

    _IDX_SQL = """
SELECT
    t.relname AS table_name, i.relname AS index_name,
    ix.indisunique AS is_unique, am.amname AS index_type,
    array_agg(a.attname ORDER BY k.ord) AS columns
FROM pg_catalog.pg_class t
JOIN pg_catalog.pg_index ix ON ix.indrelid = t.oid
JOIN pg_catalog.pg_class i ON i.oid = ix.indexrelid
JOIN pg_catalog.pg_am am ON am.oid = i.relam
JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid
JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON a.attnum = k.attnum
WHERE t.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
AND t.relkind = 'r' AND NOT ix.indisprimary
GROUP BY t.relname, i.relname, ix.indisunique, am.amname
ORDER BY t.relname, i.relname
"""

    _TBL_COMMENT_SQL = """
SELECT pgc.relname AS table_name, d.description
FROM pg_catalog.pg_class pgc
LEFT JOIN pg_catalog.pg_description d ON d.objoid = pgc.oid AND d.objsubid = 0
WHERE pgc.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
AND pgc.relkind = 'r'
"""

    async def list_tables(self) -> dict:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SET statement_timeout = '5000'")
                col_rows = await conn.fetch(self._COLS_SQL)
                idx_rows = await conn.fetch(self._IDX_SQL)
                tbl_rows = await conn.fetch(self._TBL_COMMENT_SQL)

            tbl_comments = {r["table_name"]: r["description"] for r in tbl_rows}
            indexes: dict[str, list] = {}
            for r in idx_rows:
                indexes.setdefault(r["table_name"], []).append({
                    "name": r["index_name"],
                    "type": r["index_type"],
                    "unique": r["is_unique"],
                    "columns": list(r["columns"]),
                })

            schema: dict[str, dict] = {}
            for r in col_rows:
                tbl = r["table_name"]
                if tbl not in schema:
                    schema[tbl] = {
                        "comment": tbl_comments.get(tbl),
                        "columns": [],
                        "indexes": indexes.get(tbl, []),
                    }
                col: dict = {
                    "column": r["column_name"],
                    "type": r["data_type"],
                    "nullable": r["is_nullable"] == "YES",
                    "default": r["column_default"],
                    "primary_key": r["is_primary_key"],
                }
                if r["fk_table"]:
                    col["foreign_key"] = {"table": r["fk_table"], "column": r["fk_column"]}
                if r["column_comment"]:
                    col["comment"] = r["column_comment"]
                schema[tbl]["columns"].append(col)

            return {"isError": False, "content": json.dumps(schema)}
        except asyncpg.PostgresError as e:
            return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}

    # ── get_row_count ─────────────────────────────────────────────────────────

    async def get_row_count(self, table_name: str) -> dict:
        ok, reason = self.validate_table_name(table_name)
        if not ok:
            return {"isError": True, "content": reason}
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SET statement_timeout = '5000'")
                row = await conn.fetchrow(f"SELECT COUNT(*) AS row_count FROM {table_name}")
            return {
                "isError": False,
                "content": json.dumps({"table": table_name, "row_count": row["row_count"] if row else 0}),
            }
        except asyncpg.PostgresError as e:
            return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}

    # ── describe_table ────────────────────────────────────────────────────────

    _DESCRIBE_COLS_SQL = """
WITH pk_cols AS (
    SELECT kcu.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
    WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = 'public' AND tc.table_name = $1
),
fk_cols AS (
    SELECT kcu.column_name, ccu.table_name AS ref_table, ccu.column_name AS ref_column
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name
    WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public' AND tc.table_name = $1
)
SELECT
    c.column_name, c.data_type, c.is_nullable, c.column_default,
    (pk.column_name IS NOT NULL) AS is_primary_key,
    fk.ref_table AS fk_table, fk.ref_column AS fk_column,
    d.description AS column_comment
FROM information_schema.columns c
LEFT JOIN pk_cols pk ON pk.column_name = c.column_name
LEFT JOIN fk_cols fk ON fk.column_name = c.column_name
LEFT JOIN pg_catalog.pg_class pgc ON pgc.relname = c.table_name
    AND pgc.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid = pgc.oid AND a.attname = c.column_name
LEFT JOIN pg_catalog.pg_description d ON d.objoid = pgc.oid AND d.objsubid = a.attnum
WHERE c.table_schema = 'public' AND c.table_name = $1
ORDER BY c.ordinal_position
"""

    _DESCRIBE_IDX_SQL = """
SELECT
    i.relname AS index_name, ix.indisunique AS is_unique,
    am.amname AS index_type, array_agg(a.attname ORDER BY k.ord) AS columns
FROM pg_catalog.pg_class t
JOIN pg_catalog.pg_index ix ON ix.indrelid = t.oid
JOIN pg_catalog.pg_class i ON i.oid = ix.indexrelid
JOIN pg_catalog.pg_am am ON am.oid = i.relam
JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid
JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON a.attnum = k.attnum
WHERE t.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
AND t.relkind = 'r' AND t.relname = $1 AND NOT ix.indisprimary
GROUP BY i.relname, ix.indisunique, am.amname
ORDER BY i.relname
"""

    _DESCRIBE_TBL_COMMENT_SQL = """
SELECT d.description
FROM pg_catalog.pg_class pgc
LEFT JOIN pg_catalog.pg_description d ON d.objoid = pgc.oid AND d.objsubid = 0
WHERE pgc.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
AND pgc.relkind = 'r' AND pgc.relname = $1
"""

    _ROW_COUNT_ESTIMATE_SQL = """
SELECT reltuples::bigint AS estimate
FROM pg_catalog.pg_class
WHERE relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
AND relname = $1
"""

    async def describe_table(self, table_name: str) -> dict:
        if not table_name.replace("_", "").isalnum():
            return {"isError": True, "content": "Invalid table name"}
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SET statement_timeout = '5000'")
                col_rows = await conn.fetch(self._DESCRIBE_COLS_SQL, table_name)
                if not col_rows:
                    return {"isError": True, "content": f"Table '{table_name}' not found"}
                idx_rows = await conn.fetch(self._DESCRIBE_IDX_SQL, table_name)
                tbl_row = await conn.fetchrow(self._DESCRIBE_TBL_COMMENT_SQL, table_name)
                count_row = await conn.fetchrow(self._ROW_COUNT_ESTIMATE_SQL, table_name)
                sample_rows = await conn.fetch(f'SELECT * FROM "{table_name}" LIMIT 5')

            columns = []
            for r in col_rows:
                col: dict = {
                    "column": r["column_name"],
                    "type": r["data_type"],
                    "nullable": r["is_nullable"] == "YES",
                    "default": r["column_default"],
                    "primary_key": r["is_primary_key"],
                }
                if r["fk_table"]:
                    col["foreign_key"] = {"table": r["fk_table"], "column": r["fk_column"]}
                if r["column_comment"]:
                    col["comment"] = r["column_comment"]
                columns.append(col)

            result = {
                "table": table_name,
                "comment": tbl_row["description"] if tbl_row else None,
                "row_count_estimate": count_row["estimate"] if count_row else None,
                "columns": columns,
                "indexes": [
                    {
                        "name": r["index_name"],
                        "type": r["index_type"],
                        "unique": r["is_unique"],
                        "columns": list(r["columns"]),
                    }
                    for r in idx_rows
                ],
                "sample_rows": [dict(r) for r in sample_rows],
            }
            return {"isError": False, "content": json.dumps(result, default=str)}
        except asyncpg.PostgresError as e:
            return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}

    # ── search_schema ─────────────────────────────────────────────────────────

    _SEARCH_SQL = """
WITH terms AS (SELECT string_to_array(lower($1), ' ') AS words),
table_scores AS (
    SELECT
        pgc.relname AS table_name, COALESCE(d.description, '') AS table_comment,
        (SELECT COUNT(*) FROM unnest((SELECT words FROM terms)) w
         WHERE lower(pgc.relname) LIKE '%' || w || '%'
            OR lower(COALESCE(d.description, '')) LIKE '%' || w || '%') AS score
    FROM pg_catalog.pg_class pgc
    LEFT JOIN pg_catalog.pg_description d ON d.objoid = pgc.oid AND d.objsubid = 0
    WHERE pgc.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
    AND pgc.relkind = 'r'
),
col_scores AS (
    SELECT
        c.table_name, c.column_name, c.data_type,
        COALESCE(d.description, '') AS column_comment,
        (SELECT COUNT(*) FROM unnest((SELECT words FROM terms)) w
         WHERE lower(c.column_name) LIKE '%' || w || '%'
            OR lower(COALESCE(d.description, '')) LIKE '%' || w || '%') AS score
    FROM information_schema.columns c
    LEFT JOIN pg_catalog.pg_class pgc ON pgc.relname = c.table_name
        AND pgc.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
    LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid = pgc.oid AND a.attname = c.column_name
    LEFT JOIN pg_catalog.pg_description d ON d.objoid = pgc.oid AND d.objsubid = a.attnum
    WHERE c.table_schema = 'public'
)
SELECT cs.table_name, ts.table_comment, cs.column_name, cs.data_type,
       cs.column_comment, (ts.score + cs.score) AS total_score
FROM col_scores cs
JOIN table_scores ts ON ts.table_name = cs.table_name
WHERE (ts.score + cs.score) > 0
ORDER BY total_score DESC, cs.table_name, cs.column_name
LIMIT 50
"""

    async def search_schema(self, query: str) -> dict:
        if not query or not query.strip():
            return {"isError": True, "content": "Query cannot be empty"}
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SET statement_timeout = '5000'")
                rows = await conn.fetch(self._SEARCH_SQL, query.strip())

            results: dict[str, dict] = {}
            for r in rows:
                tbl = r["table_name"]
                if tbl not in results:
                    results[tbl] = {
                        "table": tbl,
                        "table_comment": r["table_comment"] or None,
                        "matching_columns": [],
                    }
                results[tbl]["matching_columns"].append({
                    "column": r["column_name"],
                    "type": r["data_type"],
                    "comment": r["column_comment"] or None,
                })
            return {"isError": False, "content": json.dumps(list(results.values()))}
        except asyncpg.PostgresError as e:
            return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}

    # ── explain_query ─────────────────────────────────────────────────────────

    async def explain_query(self, sql: str, analyze: bool = False) -> dict:
        ok, reason = self.validate_query(sql)
        if not ok:
            return {"isError": True, "content": f"Query blocked: {reason}"}
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SET statement_timeout = '10000'")
                flag = "true" if analyze else "false"
                # EXPLAIN ANALYZE executes the query, so it gets the same read-only guard.
                async with conn.transaction(readonly=True):
                    plan_rows = await conn.fetch(f"EXPLAIN (ANALYZE {flag}, FORMAT JSON) {sql}")
            return {"isError": False, "content": json.dumps(plan_rows[0][0], default=str)}
        except asyncpg.PostgresError as e:
            return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}

    # ── get_db_health ─────────────────────────────────────────────────────────

    _UNUSED_IDX_SQL = """
SELECT relname AS table_name, indexrelname AS index_name
FROM pg_stat_user_indexes
WHERE schemaname = 'public' AND idx_scan = 0
ORDER BY relname, indexrelname
"""
    _BLOAT_SQL = """
SELECT relname AS table_name, n_live_tup, n_dead_tup,
    CASE WHEN n_live_tup > 0 THEN round(n_dead_tup::numeric / n_live_tup * 100, 1) ELSE 0 END AS dead_ratio_pct
FROM pg_stat_user_tables
WHERE schemaname = 'public' AND n_dead_tup > 1000
ORDER BY dead_ratio_pct DESC LIMIT 10
"""
    _LONG_QUERIES_SQL = """
SELECT pid, usename, state,
    round(extract(epoch FROM (now() - query_start))::numeric, 1) AS duration_sec,
    left(query, 200) AS query_snippet
FROM pg_stat_activity
WHERE state != 'idle' AND query_start IS NOT NULL
AND query NOT ILIKE '%pg_stat_activity%'
ORDER BY duration_sec DESC LIMIT 10
"""
    _CONNECTIONS_SQL = """
SELECT count(*) AS current_connections,
    (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') AS max_connections
FROM pg_stat_activity
"""

    async def get_db_health(self) -> dict:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SET statement_timeout = '10000'")
                unused_idx = await conn.fetch(self._UNUSED_IDX_SQL)
                bloat = await conn.fetch(self._BLOAT_SQL)
                long_queries = await conn.fetch(self._LONG_QUERIES_SQL)
                conn_row = await conn.fetchrow(self._CONNECTIONS_SQL)

            current = conn_row["current_connections"]
            maximum = conn_row["max_connections"]
            result = {
                "connections": {
                    "current": current,
                    "max": maximum,
                    "utilization_pct": round(current / maximum * 100, 1) if maximum else None,
                },
                "unused_indexes": [
                    {"table": r["table_name"], "index": r["index_name"]} for r in unused_idx
                ],
                "table_bloat": [
                    {
                        "table": r["table_name"],
                        "live_rows": r["n_live_tup"],
                        "dead_rows": r["n_dead_tup"],
                        "dead_ratio_pct": float(r["dead_ratio_pct"]),
                    }
                    for r in bloat
                ],
                "long_running_queries": [
                    {
                        "pid": r["pid"],
                        "user": r["usename"],
                        "state": r["state"],
                        "duration_sec": float(r["duration_sec"]),
                        "query": r["query_snippet"],
                    }
                    for r in long_queries
                ],
            }
            return {"isError": False, "content": json.dumps(result, default=str)}
        except asyncpg.PostgresError as e:
            return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}

    # ── get_slow_queries ──────────────────────────────────────────────────────

    _CHECK_EXT_SQL = "SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'"
    _SLOW_QUERIES_SQL = """
SELECT left(query, 300) AS query_snippet, calls,
    round(mean_exec_time::numeric, 2) AS mean_exec_ms,
    round(total_exec_time::numeric, 2) AS total_exec_ms, rows
FROM pg_stat_statements
WHERE query NOT ILIKE '%pg_stat_statements%'
ORDER BY total_exec_time DESC
LIMIT $1
"""

    async def get_slow_queries(self, limit: int = 10) -> dict:
        if not 1 <= limit <= 100:
            return {"isError": True, "content": "limit must be between 1 and 100"}
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SET statement_timeout = '10000'")
                ext = await conn.fetchrow(self._CHECK_EXT_SQL)
                if not ext:
                    return {
                        "isError": True,
                        "content": (
                            "pg_stat_statements extension is not installed. "
                            "Enable it with: CREATE EXTENSION pg_stat_statements;"
                        ),
                    }
                rows = await conn.fetch(self._SLOW_QUERIES_SQL, limit)

            return {
                "isError": False,
                "content": json.dumps([
                    {
                        "query": r["query_snippet"],
                        "calls": r["calls"],
                        "mean_exec_ms": float(r["mean_exec_ms"]),
                        "total_exec_ms": float(r["total_exec_ms"]),
                        "rows": r["rows"],
                    }
                    for r in rows
                ]),
            }
        except asyncpg.PostgresError as e:
            return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}

    # ── annotate ─────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_name(name: str) -> bool:
        return name.replace("_", "").isalnum()

    @staticmethod
    def _escape(s: str) -> str:
        return s.replace("'", "''")

    async def annotate_table(self, table_name: str, comment: str) -> dict:
        if not self._safe_name(table_name):
            return {"isError": True, "content": "Invalid table name"}
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SET statement_timeout = '5000'")
                await conn.execute(
                    f"COMMENT ON TABLE \"{table_name}\" IS '{self._escape(comment)}'"
                )
            return {"isError": False, "content": f"Comment set on table '{table_name}'"}
        except asyncpg.PostgresError as e:
            return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}

    async def annotate_column(self, table_name: str, column_name: str, comment: str) -> dict:
        if not self._safe_name(table_name) or not self._safe_name(column_name):
            return {"isError": True, "content": "Invalid table or column name"}
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SET statement_timeout = '5000'")
                await conn.execute(
                    f"COMMENT ON COLUMN \"{table_name}\".\"{column_name}\" IS '{self._escape(comment)}'"
                )
            return {"isError": False, "content": f"Comment set on '{table_name}.{column_name}'"}
        except asyncpg.PostgresError as e:
            return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}
