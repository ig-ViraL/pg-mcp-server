import json
import asyncpg
from src.safety.validator import validate_query, inject_limit
from src.db.pool import get_pool


async def handle_run_query(sql: str, limit: int = 100) -> dict:
    ok, reason = validate_query(sql)
    if not ok:
        return {"isError": True, "content": reason}

    sql = inject_limit(sql, limit)

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET statement_timeout = '5000'")
            rows = await conn.fetch(sql)
        records = [dict(r) for r in rows]
        return {
            "isError": False,
            "content": json.dumps({"rows": records, "row_count": len(records)}, default=str),
        }
    except asyncpg.exceptions.QueryCanceledError:
        return {"isError": True, "content": "Query timed out after 5 seconds"}
    except asyncpg.PostgresError as e:
        return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}
