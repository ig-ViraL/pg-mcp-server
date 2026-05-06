import json
import asyncpg
from src.db.pool import get_pool
from src.safety.validator import validate_query


async def handle_explain_query(sql: str, analyze: bool = False) -> dict:
    safe, reason = validate_query(sql)
    if not safe:
        return {"isError": True, "content": f"Query blocked: {reason}"}
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET statement_timeout = '10000'")
            analyze_flag = "true" if analyze else "false"
            plan_rows = await conn.fetch(
                f"EXPLAIN (ANALYZE {analyze_flag}, FORMAT JSON) {sql}"
            )
        plan = plan_rows[0][0]
        return {"isError": False, "content": json.dumps(plan, default=str)}
    except asyncpg.PostgresError as e:
        return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}
