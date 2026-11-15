import json
import aiosqlite
from .base import BaseDriver


def _parse_path(url: str) -> str:
    """Extract file path from sqlite:///path or sqlite+aiosqlite:///path."""
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            return url[len(prefix):]
    return url


class SQLiteDriver(BaseDriver):
    """SQLite driver using aiosqlite."""

    def __init__(self) -> None:
        self._path: str = ""

    async def connect(self, url: str) -> None:
        self._path = _parse_path(url)

    async def close(self) -> None:
        pass  # aiosqlite opens per-query connections; nothing to pool

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _fetch(self, sql: str, args: tuple = ()) -> list[dict]:
        async with aiosqlite.connect(self._path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(sql, args) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def _fetchone(self, sql: str, args: tuple = ()) -> dict | None:
        rows = await self._fetch(sql, args)
        return rows[0] if rows else None

    async def _execute(self, sql: str, args: tuple = ()) -> None:
        async with aiosqlite.connect(self._path) as conn:
            await conn.execute(sql, args)
            await conn.commit()

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

    async def list_tables(self) -> dict:
        try:
            tables = await self._fetch(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            schema: dict[str, dict] = {}
            for t in tables:
                tbl = t["name"]
                cols_raw = await self._fetch(f"PRAGMA table_info(`{tbl}`)")
                fks_raw = await self._fetch(f"PRAGMA foreign_key_list(`{tbl}`)")
                idx_list = await self._fetch(f"PRAGMA index_list(`{tbl}`)")

                fk_map = {r["from"]: {"table": r["table"], "column": r["to"]} for r in fks_raw}

                columns = []
                for r in cols_raw:
                    col: dict = {
                        "column": r["name"],
                        "type": r["type"],
                        "nullable": not r["notnull"],
                        "default": r["dflt_value"],
                        "primary_key": bool(r["pk"]),
                    }
                    if r["name"] in fk_map:
                        col["foreign_key"] = fk_map[r["name"]]
                    columns.append(col)

                indexes = []
                for ix in idx_list:
                    if ix["origin"] == "pk":
                        continue
                    ix_cols = await self._fetch(f"PRAGMA index_info(`{ix['name']}`)")
                    indexes.append({
                        "name": ix["name"],
                        "type": "INDEX",
                        "unique": bool(ix["unique"]),
                        "columns": [c["name"] for c in ix_cols],
                    })

                schema[tbl] = {"comment": None, "columns": columns, "indexes": indexes}

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

    async def describe_table(self, table_name: str) -> dict:
        if not table_name.replace("_", "").isalnum():
            return {"isError": True, "content": "Invalid table name"}
        try:
            cols_raw = await self._fetch(f"PRAGMA table_info(`{table_name}`)")
            if not cols_raw:
                return {"isError": True, "content": f"Table '{table_name}' not found"}

            fks_raw = await self._fetch(f"PRAGMA foreign_key_list(`{table_name}`)")
            idx_list = await self._fetch(f"PRAGMA index_list(`{table_name}`)")
            count_row = await self._fetchone(f"SELECT COUNT(*) AS cnt FROM `{table_name}`")
            sample_rows = await self._fetch(f"SELECT * FROM `{table_name}` LIMIT 5")

            fk_map = {r["from"]: {"table": r["table"], "column": r["to"]} for r in fks_raw}

            columns = []
            for r in cols_raw:
                col: dict = {
                    "column": r["name"],
                    "type": r["type"],
                    "nullable": not r["notnull"],
                    "default": r["dflt_value"],
                    "primary_key": bool(r["pk"]),
                }
                if r["name"] in fk_map:
                    col["foreign_key"] = fk_map[r["name"]]
                columns.append(col)

            indexes = []
            for ix in idx_list:
                if ix["origin"] == "pk":
                    continue
                ix_cols = await self._fetch(f"PRAGMA index_info(`{ix['name']}`)")
                indexes.append({
                    "name": ix["name"],
                    "type": "INDEX",
                    "unique": bool(ix["unique"]),
                    "columns": [c["name"] for c in ix_cols],
                })

            result = {
                "table": table_name,
                "comment": None,
                "row_count_estimate": count_row["cnt"] if count_row else None,
                "columns": columns,
                "indexes": indexes,
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
            tables = await self._fetch(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )

            results: dict[str, dict] = {}
            for t in tables:
                tbl = t["name"]
                cols_raw = await self._fetch(f"PRAGMA table_info(`{tbl}`)")
                for r in cols_raw:
                    col_name = r["name"].lower()
                    tbl_lower = tbl.lower()
                    if any(w in tbl_lower or w in col_name for w in words):
                        if tbl not in results:
                            results[tbl] = {
                                "table": tbl,
                                "table_comment": None,
                                "matching_columns": [],
                            }
                        results[tbl]["matching_columns"].append({
                            "column": r["name"],
                            "type": r["type"],
                            "comment": None,
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
            rows = await self._fetch(f"EXPLAIN QUERY PLAN {sql}")
            return {"isError": False, "content": json.dumps(rows, default=str)}
        except Exception as e:
            return self._err(e)

    # ── get_db_health ─────────────────────────────────────────────────────────

    async def get_db_health(self) -> dict:
        try:
            integrity = await self._fetch("PRAGMA integrity_check")
            page_count = await self._fetchone("PRAGMA page_count")
            page_size = await self._fetchone("PRAGMA page_size")
            freelist = await self._fetchone("PRAGMA freelist_count")

            pages = page_count["page_count"] if page_count else 0
            size = page_size["page_size"] if page_size else 4096
            free = freelist["freelist_count"] if freelist else 0

            result = {
                "engine": "sqlite",
                "integrity": integrity[0]["integrity_check"] if integrity else "unknown",
                "database_size_bytes": pages * size,
                "free_pages": free,
                "fragmentation_pct": round(free / pages * 100, 1) if pages else 0,
            }
            return {"isError": False, "content": json.dumps(result, default=str)}
        except Exception as e:
            return self._err(e)

    # ── get_slow_queries ──────────────────────────────────────────────────────

    async def get_slow_queries(self, limit: int = 10) -> dict:
        return self._not_supported("get_slow_queries")

    # ── annotate ─────────────────────────────────────────────────────────────

    async def annotate_table(self, table_name: str, comment: str) -> dict:
        return self._not_supported("annotate_table (SQLite has no native COMMENT support)")

    async def annotate_column(self, table_name: str, column_name: str, comment: str) -> dict:
        return self._not_supported("annotate_column (SQLite has no native COMMENT support)")
