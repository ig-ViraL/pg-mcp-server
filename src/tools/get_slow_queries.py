import json
import asyncpg
from src.db.pool import get_pool

_CHECK_EXT_SQL = """
SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'
"""

_SLOW_QUERIES_SQL = """
SELECT
    left(query, 300) AS query_snippet,
    calls,
    round(mean_exec_time::numeric, 2) AS mean_exec_ms,
    round(total_exec_time::numeric, 2) AS total_exec_ms,
    rows
FROM pg_stat_statements
WHERE query NOT ILIKE '%pg_stat_statements%'
ORDER BY total_exec_time DESC
LIMIT $1
"""


async def handle_get_slow_queries(limit: int = 10) -> dict:
    if not 1 <= limit <= 100:
        return {"isError": True, "content": "limit must be between 1 and 100"}
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET statement_timeout = '10000'")
            ext = await conn.fetchrow(_CHECK_EXT_SQL)
            if not ext:
                return {
                    "isError": True,
                    "content": (
                        "pg_stat_statements extension is not installed. "
                        "Enable it with: CREATE EXTENSION pg_stat_statements; "
                        "and add it to shared_preload_libraries in postgresql.conf."
                    ),
                }
            rows = await conn.fetch(_SLOW_QUERIES_SQL, limit)

        result = [
            {
                "query": r["query_snippet"],
                "calls": r["calls"],
                "mean_exec_ms": float(r["mean_exec_ms"]),
                "total_exec_ms": float(r["total_exec_ms"]),
                "rows": r["rows"],
            }
            for r in rows
        ]
        return {"isError": False, "content": json.dumps(result)}
    except asyncpg.PostgresError as e:
        return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}
