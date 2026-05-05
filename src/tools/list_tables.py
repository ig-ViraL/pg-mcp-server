import json
import asyncpg
from src.db.pool import get_pool

_SQL = """
SELECT
    c.table_name,
    c.column_name,
    c.data_type
FROM information_schema.columns c
WHERE c.table_schema = 'public'
ORDER BY c.table_name, c.ordinal_position
"""


async def handle_list_tables() -> dict:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET statement_timeout = '5000'")
            rows = await conn.fetch(_SQL)

        schema: dict[str, list] = {}
        for row in rows:
            tbl = row["table_name"]
            schema.setdefault(tbl, []).append(
                {"column": row["column_name"], "type": row["data_type"]}
            )

        return {"isError": False, "content": json.dumps(schema)}
    except asyncpg.PostgresError as e:
        return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}
