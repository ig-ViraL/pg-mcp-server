import asyncpg
from src.db.pool import get_pool


def _safe_name(name: str) -> bool:
    return name.replace("_", "").isalnum()


def _escape(s: str) -> str:
    return s.replace("'", "''")


async def handle_annotate_table(table_name: str, comment: str) -> dict:
    if not _safe_name(table_name):
        return {"isError": True, "content": "Invalid table name"}
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET statement_timeout = '5000'")
            await conn.execute(
                f"COMMENT ON TABLE \"{table_name}\" IS '{_escape(comment)}'"
            )
        return {"isError": False, "content": f"Comment set on table '{table_name}'"}
    except asyncpg.PostgresError as e:
        return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}


async def handle_annotate_column(table_name: str, column_name: str, comment: str) -> dict:
    if not _safe_name(table_name) or not _safe_name(column_name):
        return {"isError": True, "content": "Invalid table or column name"}
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET statement_timeout = '5000'")
            await conn.execute(
                f"COMMENT ON COLUMN \"{table_name}\".\"{column_name}\" IS '{_escape(comment)}'"
            )
        return {"isError": False, "content": f"Comment set on '{table_name}.{column_name}'"}
    except asyncpg.PostgresError as e:
        return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}
