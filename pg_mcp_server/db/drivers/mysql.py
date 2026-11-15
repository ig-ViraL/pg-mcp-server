import json
from urllib.parse import urlparse
import aiomysql
from .base import BaseDriver


def _parse_url(url: str) -> dict:
    """Parse mysql://user:pass@host:port/dbname into aiomysql kwargs."""
    # Strip driver prefix variants like mysql+asyncmy://
    clean = url.replace("mysql+asyncmy://", "mysql://").replace("mariadb://", "mysql://")
    p = urlparse(clean)
    return {
        "host": p.hostname or "127.0.0.1",
        "port": p.port or 3306,
        "user": p.username,
        "password": p.password or "",
        "db": p.path.lstrip("/"),
        "autocommit": True,
        "charset": "utf8mb4",
    }


class MySQLDriver(BaseDriver):
    """MySQL / MariaDB driver using aiomysql."""

    def __init__(self) -> None:
        self._pool: aiomysql.Pool | None = None

    async def connect(self, url: str) -> None:
        kwargs = _parse_url(url)
        self._pool = await aiomysql.create_pool(minsize=1, maxsize=5, **kwargs)

    async def close(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _fetch(self, sql: str, args: tuple = ()) -> list[dict]:
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SET SESSION max_execution_time = 5000")
                await cur.execute(sql, args)
                return list(await cur.fetchall())

    async def _fetchone(self, sql: str, args: tuple = ()) -> dict | None:
        rows = await self._fetch(sql, args)
        return rows[0] if rows else None

    async def _execute(self, sql: str, args: tuple = (), timeout_ms: int = 5000) -> None:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"SET SESSION max_execution_time = {timeout_ms}")
                await cur.execute(sql, args)

    @staticmethod
    def _err(e: Exception) -> dict:
        return {"isError": True, "content": f"Database error: {e}"}

    # ── run_query ─────────────────────────────────────────────────────────────

    async def run_query(self, sql: str, limit: int = 100) -> dict:
        ok, reason = self.validate_query(sql)
        if not ok:
            return {"isError": True, "content": reason}
        sql = self.inject_limit(sql, limit)
        try:
            rows = await self._fetch(sql)
            return {
                "isError": False,
                "content": json.dumps({"rows": rows, "row_count": len(rows)}, default=str),
            }
        except Exception as e:
            return self._err(e)

    # ── list_tables ───────────────────────────────────────────────────────────

    _COLS_SQL = """
SELECT
    c.TABLE_NAME       AS table_name,
    c.COLUMN_NAME      AS column_name,
    c.DATA_TYPE        AS data_type,
    c.IS_NULLABLE      AS is_nullable,
    c.COLUMN_DEFAULT   AS column_default,
    c.COLUMN_COMMENT   AS column_comment,
    (c.COLUMN_KEY = 'PRI') AS is_primary_key,
    kcu.REFERENCED_TABLE_NAME  AS fk_table,
    kcu.REFERENCED_COLUMN_NAME AS fk_column,
    t.TABLE_COMMENT    AS table_comment
FROM information_schema.COLUMNS c
JOIN information_schema.TABLES t
    ON t.TABLE_NAME = c.TABLE_NAME AND t.TABLE_SCHEMA = c.TABLE_SCHEMA
    AND t.TABLE_TYPE = 'BASE TABLE'
LEFT JOIN information_schema.KEY_COLUMN_USAGE kcu
    ON kcu.TABLE_NAME = c.TABLE_NAME
    AND kcu.COLUMN_NAME = c.COLUMN_NAME
    AND kcu.TABLE_SCHEMA = c.TABLE_SCHEMA
    AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
WHERE c.TABLE_SCHEMA = DATABASE()
ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION
"""

    _IDX_SQL = """
SELECT
    TABLE_NAME  AS table_name,
    INDEX_NAME  AS index_name,
    NOT NON_UNIQUE AS is_unique,
    INDEX_TYPE  AS index_type,
    GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE() AND INDEX_NAME != 'PRIMARY'
GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE, INDEX_TYPE
ORDER BY TABLE_NAME, INDEX_NAME
"""

    async def list_tables(self) -> dict:
        try:
            col_rows = await self._fetch(self._COLS_SQL)
            idx_rows = await self._fetch(self._IDX_SQL)

            indexes: dict[str, list] = {}
            for r in idx_rows:
                indexes.setdefault(r["table_name"], []).append({
                    "name": r["index_name"],
                    "type": r["index_type"],
                    "unique": bool(r["is_unique"]),
                    "columns": r["columns"].split(",") if r["columns"] else [],
                })

            schema: dict[str, dict] = {}
            for r in col_rows:
                tbl = r["table_name"]
                if tbl not in schema:
                    schema[tbl] = {
                        "comment": r["table_comment"] or None,
                        "columns": [],
                        "indexes": indexes.get(tbl, []),
                    }
                col: dict = {
                    "column": r["column_name"],
                    "type": r["data_type"],
                    "nullable": r["is_nullable"] == "YES",
                    "default": r["column_default"],
                    "primary_key": bool(r["is_primary_key"]),
                }
                if r["fk_table"]:
                    col["foreign_key"] = {"table": r["fk_table"], "column": r["fk_column"]}
                if r["column_comment"]:
                    col["comment"] = r["column_comment"]
                schema[tbl]["columns"].append(col)

            return {"isError": False, "content": json.dumps(schema)}
        except Exception as e:
            return self._err(e)

    # ── get_row_count ─────────────────────────────────────────────────────────

    async def get_row_count(self, table_name: str) -> dict:
        ok, reason = self.validate_table_name(table_name)
        if not ok:
            return {"isError": True, "content": reason}
        try:
            row = await self._fetchone(f"SELECT COUNT(*) AS row_count FROM `{table_name}`")
            return {
                "isError": False,
                "content": json.dumps({"table": table_name, "row_count": row["row_count"] if row else 0}),
            }
        except Exception as e:
            return self._err(e)

    # ── describe_table ────────────────────────────────────────────────────────

    _DESCRIBE_COLS_SQL = """
SELECT
    c.COLUMN_NAME      AS column_name,
    c.DATA_TYPE        AS data_type,
    c.IS_NULLABLE      AS is_nullable,
    c.COLUMN_DEFAULT   AS column_default,
    c.COLUMN_COMMENT   AS column_comment,
    (c.COLUMN_KEY = 'PRI') AS is_primary_key,
    kcu.REFERENCED_TABLE_NAME  AS fk_table,
    kcu.REFERENCED_COLUMN_NAME AS fk_column
FROM information_schema.COLUMNS c
LEFT JOIN information_schema.KEY_COLUMN_USAGE kcu
    ON kcu.TABLE_NAME = c.TABLE_NAME
    AND kcu.COLUMN_NAME = c.COLUMN_NAME
    AND kcu.TABLE_SCHEMA = c.TABLE_SCHEMA
    AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
WHERE c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = %s
ORDER BY c.ORDINAL_POSITION
"""

    _DESCRIBE_IDX_SQL = """
SELECT INDEX_NAME AS index_name, NOT NON_UNIQUE AS is_unique,
    INDEX_TYPE AS index_type,
    GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME != 'PRIMARY'
GROUP BY INDEX_NAME, NON_UNIQUE, INDEX_TYPE
ORDER BY INDEX_NAME
"""

    _DESCRIBE_TBL_SQL = """
SELECT TABLE_COMMENT AS table_comment, TABLE_ROWS AS row_count_estimate
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
"""

    async def describe_table(self, table_name: str) -> dict:
        if not table_name.replace("_", "").isalnum():
            return {"isError": True, "content": "Invalid table name"}
        try:
            col_rows = await self._fetch(self._DESCRIBE_COLS_SQL, (table_name,))
            if not col_rows:
                return {"isError": True, "content": f"Table '{table_name}' not found"}
            idx_rows = await self._fetch(self._DESCRIBE_IDX_SQL, (table_name,))
            tbl_row = await self._fetchone(self._DESCRIBE_TBL_SQL, (table_name,))
            sample_rows = await self._fetch(f"SELECT * FROM `{table_name}` LIMIT 5")

            columns = []
            for r in col_rows:
                col: dict = {
                    "column": r["column_name"],
                    "type": r["data_type"],
                    "nullable": r["is_nullable"] == "YES",
                    "default": r["column_default"],
                    "primary_key": bool(r["is_primary_key"]),
                }
                if r["fk_table"]:
                    col["foreign_key"] = {"table": r["fk_table"], "column": r["fk_column"]}
                if r["column_comment"]:
                    col["comment"] = r["column_comment"]
                columns.append(col)

            result = {
                "table": table_name,
                "comment": tbl_row["table_comment"] if tbl_row else None,
                "row_count_estimate": tbl_row["row_count_estimate"] if tbl_row else None,
                "columns": columns,
                "indexes": [
                    {
                        "name": r["index_name"],
                        "type": r["index_type"],
                        "unique": bool(r["is_unique"]),
                        "columns": r["columns"].split(",") if r["columns"] else [],
                    }
                    for r in idx_rows
                ],
                "sample_rows": sample_rows,
            }
            return {"isError": False, "content": json.dumps(result, default=str)}
        except Exception as e:
            return self._err(e)

    # ── search_schema ─────────────────────────────────────────────────────────

    async def search_schema(self, query: str) -> dict:
        if not query or not query.strip():
            return {"isError": True, "content": "Query cannot be empty"}
        try:
            words = query.strip().lower().split()
            like_clauses = " OR ".join(
                "(LOWER(c.TABLE_NAME) LIKE %s OR LOWER(c.COLUMN_NAME) LIKE %s "
                "OR LOWER(c.COLUMN_COMMENT) LIKE %s OR LOWER(t.TABLE_COMMENT) LIKE %s)"
                for _ in words
            )
            args = tuple(f"%{w}%" for w in words for _ in range(4))
            sql = f"""
SELECT c.TABLE_NAME AS table_name, t.TABLE_COMMENT AS table_comment,
       c.COLUMN_NAME AS column_name, c.DATA_TYPE AS data_type,
       c.COLUMN_COMMENT AS column_comment
FROM information_schema.COLUMNS c
JOIN information_schema.TABLES t
    ON t.TABLE_NAME = c.TABLE_NAME AND t.TABLE_SCHEMA = c.TABLE_SCHEMA
    AND t.TABLE_TYPE = 'BASE TABLE'
WHERE c.TABLE_SCHEMA = DATABASE() AND ({like_clauses})
ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION
LIMIT 50
"""
            rows = await self._fetch(sql, args)

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
        except Exception as e:
            return self._err(e)

    # ── explain_query ─────────────────────────────────────────────────────────

    async def explain_query(self, sql: str, analyze: bool = False) -> dict:
        ok, reason = self.validate_query(sql)
        if not ok:
            return {"isError": True, "content": f"Query blocked: {reason}"}
        try:
            # MySQL 8.0+ supports EXPLAIN ANALYZE; fall back to EXPLAIN FORMAT=JSON
            prefix = "EXPLAIN ANALYZE" if analyze else "EXPLAIN FORMAT=JSON"
            rows = await self._fetch(f"{prefix} {sql}")
            return {"isError": False, "content": json.dumps(rows, default=str)}
        except Exception as e:
            return self._err(e)

    # ── get_db_health ─────────────────────────────────────────────────────────

    async def get_db_health(self) -> dict:
        try:
            # Fragmentation from information_schema
            bloat_rows = await self._fetch("""
SELECT TABLE_NAME AS table_name, TABLE_ROWS AS live_rows,
    DATA_FREE AS data_free_bytes,
    ROUND(DATA_FREE / (DATA_LENGTH + INDEX_LENGTH + DATA_FREE + 1) * 100, 1) AS frag_pct
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE() AND DATA_FREE > 0
ORDER BY frag_pct DESC LIMIT 10
""")
            # Long running queries
            long_rows = await self._fetch("""
SELECT ID AS pid, USER AS user, STATE AS state,
    TIME AS duration_sec, LEFT(INFO, 200) AS query_snippet
FROM information_schema.PROCESSLIST
WHERE COMMAND != 'Sleep' AND INFO IS NOT NULL
ORDER BY TIME DESC LIMIT 10
""")
            # Connection counts
            conn_row = await self._fetchone(
                "SELECT VARIABLE_VALUE AS current_connections "
                "FROM performance_schema.global_status "
                "WHERE VARIABLE_NAME = 'Threads_connected'"
            )
            max_row = await self._fetchone(
                "SELECT VARIABLE_VALUE AS max_connections "
                "FROM performance_schema.global_variables "
                "WHERE VARIABLE_NAME = 'max_connections'"
            )

            current = int(conn_row["current_connections"]) if conn_row else 0
            maximum = int(max_row["max_connections"]) if max_row else 0

            result = {
                "connections": {
                    "current": current,
                    "max": maximum,
                    "utilization_pct": round(current / maximum * 100, 1) if maximum else None,
                },
                "table_fragmentation": [
                    {
                        "table": r["table_name"],
                        "live_rows": r["live_rows"],
                        "data_free_bytes": r["data_free_bytes"],
                        "frag_pct": float(r["frag_pct"] or 0),
                    }
                    for r in bloat_rows
                ],
                "long_running_queries": [
                    {
                        "pid": r["pid"],
                        "user": r["user"],
                        "state": r["state"],
                        "duration_sec": r["duration_sec"],
                        "query": r["query_snippet"],
                    }
                    for r in long_rows
                ],
            }
            return {"isError": False, "content": json.dumps(result, default=str)}
        except Exception as e:
            return self._err(e)

    # ── get_slow_queries ──────────────────────────────────────────────────────

    async def get_slow_queries(self, limit: int = 10) -> dict:
        if not 1 <= limit <= 100:
            return {"isError": True, "content": "limit must be between 1 and 100"}
        try:
            # Check performance_schema is on
            ps_row = await self._fetchone(
                "SELECT VARIABLE_VALUE AS val FROM performance_schema.global_variables "
                "WHERE VARIABLE_NAME = 'performance_schema'"
            )
            if not ps_row or ps_row["val"].upper() != "ON":
                return {
                    "isError": True,
                    "content": "performance_schema is not enabled. Enable it in my.cnf: performance_schema=ON",
                }
            rows = await self._fetch(f"""
SELECT LEFT(DIGEST_TEXT, 300) AS query_snippet,
    COUNT_STAR AS calls,
    ROUND(AVG_TIMER_WAIT / 1e9, 2) AS mean_exec_ms,
    ROUND(SUM_TIMER_WAIT / 1e9, 2) AS total_exec_ms,
    SUM_ROWS_SENT AS rows
FROM performance_schema.events_statements_summary_by_digest
WHERE DIGEST_TEXT IS NOT NULL
ORDER BY SUM_TIMER_WAIT DESC
LIMIT {int(limit)}
""")
            return {"isError": False, "content": json.dumps(rows, default=str)}
        except Exception as e:
            return self._err(e)

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
            await self._execute(
                f"ALTER TABLE `{table_name}` COMMENT = '{self._escape(comment)}'"
            )
            return {"isError": False, "content": f"Comment set on table '{table_name}'"}
        except Exception as e:
            return self._err(e)

    async def annotate_column(self, table_name: str, column_name: str, comment: str) -> dict:
        if not self._safe_name(table_name) or not self._safe_name(column_name):
            return {"isError": True, "content": "Invalid table or column name"}
        try:
            # Must preserve the column definition — fetch it first
            col = await self._fetchone(
                "SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
                (table_name, column_name),
            )
            if not col:
                return {"isError": True, "content": f"Column '{table_name}.{column_name}' not found"}

            nullable = "" if col["IS_NULLABLE"] == "YES" else "NOT NULL"
            default = (
                f"DEFAULT '{self._escape(str(col['COLUMN_DEFAULT']))}'"
                if col["COLUMN_DEFAULT"] is not None
                else ""
            )
            await self._execute(
                f"ALTER TABLE `{table_name}` MODIFY COLUMN `{column_name}` "
                f"{col['COLUMN_TYPE']} {nullable} {default} "
                f"COMMENT '{self._escape(comment)}'"
            )
            return {"isError": False, "content": f"Comment set on '{table_name}.{column_name}'"}
        except Exception as e:
            return self._err(e)
