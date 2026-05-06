import json
import asyncpg
from src.db.pool import get_pool

_UNUSED_IDX_SQL = """
SELECT schemaname, relname AS table_name, indexrelname AS index_name, idx_scan
FROM pg_stat_user_indexes
WHERE schemaname = 'public' AND idx_scan = 0
ORDER BY relname, indexrelname
"""

_BLOAT_SQL = """
SELECT
    relname AS table_name,
    n_live_tup,
    n_dead_tup,
    CASE WHEN n_live_tup > 0
        THEN round(n_dead_tup::numeric / n_live_tup * 100, 1)
        ELSE 0
    END AS dead_ratio_pct
FROM pg_stat_user_tables
WHERE schemaname = 'public' AND n_dead_tup > 1000
ORDER BY dead_ratio_pct DESC
LIMIT 10
"""

_LONG_QUERIES_SQL = """
SELECT
    pid,
    usename,
    state,
    round(extract(epoch FROM (now() - query_start))::numeric, 1) AS duration_sec,
    left(query, 200) AS query_snippet
FROM pg_stat_activity
WHERE state != 'idle'
AND query_start IS NOT NULL
AND query NOT ILIKE '%pg_stat_activity%'
ORDER BY duration_sec DESC
LIMIT 10
"""

_CONNECTIONS_SQL = """
SELECT
    count(*) AS current_connections,
    (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') AS max_connections
FROM pg_stat_activity
"""


async def handle_get_db_health() -> dict:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET statement_timeout = '10000'")
            unused_idx = await conn.fetch(_UNUSED_IDX_SQL)
            bloat = await conn.fetch(_BLOAT_SQL)
            long_queries = await conn.fetch(_LONG_QUERIES_SQL)
            conn_row = await conn.fetchrow(_CONNECTIONS_SQL)

        current = conn_row["current_connections"]
        maximum = conn_row["max_connections"]
        result = {
            "connections": {
                "current": current,
                "max": maximum,
                "utilization_pct": round(current / maximum * 100, 1) if maximum else None,
            },
            "unused_indexes": [
                {"table": r["table_name"], "index": r["index_name"]}
                for r in unused_idx
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
