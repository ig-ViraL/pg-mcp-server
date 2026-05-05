import json
import asyncpg
from src.safety.validator import validate_table_name
from src.db.pool import get_pool

_SQL = "SELECT COUNT(*) AS row_count FROM {}"


async def handle_get_row_count(table_name: str) -> dict:
    ok, reason = validate_table_name(table_name)
    if not ok:
        return {"isError": True, "content": reason}

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET statement_timeout = '5000'")
            row = await conn.fetchrow(_SQL.format(table_name))

        count = row["row_count"] if row else 0
        return {
            "isError": False,
            "content": json.dumps({"table": table_name, "approximate_row_count": count}),
        }
    except asyncpg.PostgresError as e:
        return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}
