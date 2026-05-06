import json
import asyncpg
from src.db.pool import get_pool

_SQL = """
WITH terms AS (
    SELECT string_to_array(lower($1), ' ') AS words
),
table_scores AS (
    SELECT
        pgc.relname AS table_name,
        COALESCE(d.description, '') AS table_comment,
        (
            SELECT COUNT(*) FROM unnest((SELECT words FROM terms)) w
            WHERE lower(pgc.relname) LIKE '%' || w || '%'
               OR lower(COALESCE(d.description, '')) LIKE '%' || w || '%'
        ) AS score
    FROM pg_catalog.pg_class pgc
    LEFT JOIN pg_catalog.pg_description d ON d.objoid = pgc.oid AND d.objsubid = 0
    WHERE pgc.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
    AND pgc.relkind = 'r'
),
col_scores AS (
    SELECT
        c.table_name,
        c.column_name,
        c.data_type,
        COALESCE(d.description, '') AS column_comment,
        (
            SELECT COUNT(*) FROM unnest((SELECT words FROM terms)) w
            WHERE lower(c.column_name) LIKE '%' || w || '%'
               OR lower(COALESCE(d.description, '')) LIKE '%' || w || '%'
        ) AS score
    FROM information_schema.columns c
    LEFT JOIN pg_catalog.pg_class pgc ON pgc.relname = c.table_name
        AND pgc.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
    LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid = pgc.oid AND a.attname = c.column_name
    LEFT JOIN pg_catalog.pg_description d ON d.objoid = pgc.oid AND d.objsubid = a.attnum
    WHERE c.table_schema = 'public'
)
SELECT
    cs.table_name,
    ts.table_comment,
    cs.column_name,
    cs.data_type,
    cs.column_comment,
    (ts.score + cs.score) AS total_score
FROM col_scores cs
JOIN table_scores ts ON ts.table_name = cs.table_name
WHERE (ts.score + cs.score) > 0
ORDER BY total_score DESC, cs.table_name, cs.column_name
LIMIT 50
"""


async def handle_search_schema(query: str) -> dict:
    if not query or not query.strip():
        return {"isError": True, "content": "Query cannot be empty"}
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET statement_timeout = '5000'")
            rows = await conn.fetch(_SQL, query.strip())

        # Group by table
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
