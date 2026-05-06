import json
import asyncpg
from src.db.pool import get_pool

_COLS_SQL = """
WITH pk_cols AS (
    SELECT kcu.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
    WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = 'public' AND tc.table_name = $1
),
fk_cols AS (
    SELECT kcu.column_name, ccu.table_name AS ref_table, ccu.column_name AS ref_column
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name
    WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public' AND tc.table_name = $1
)
SELECT
    c.column_name,
    c.data_type,
    c.is_nullable,
    c.column_default,
    (pk.column_name IS NOT NULL) AS is_primary_key,
    fk.ref_table AS fk_table,
    fk.ref_column AS fk_column,
    d.description AS column_comment
FROM information_schema.columns c
LEFT JOIN pk_cols pk ON pk.column_name = c.column_name
LEFT JOIN fk_cols fk ON fk.column_name = c.column_name
LEFT JOIN pg_catalog.pg_class pgc ON pgc.relname = c.table_name
    AND pgc.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid = pgc.oid AND a.attname = c.column_name
LEFT JOIN pg_catalog.pg_description d ON d.objoid = pgc.oid AND d.objsubid = a.attnum
WHERE c.table_schema = 'public' AND c.table_name = $1
ORDER BY c.ordinal_position
"""

_IDX_SQL = """
SELECT
    i.relname AS index_name,
    ix.indisunique AS is_unique,
    am.amname AS index_type,
    array_agg(a.attname ORDER BY k.ord) AS columns
FROM pg_catalog.pg_class t
JOIN pg_catalog.pg_index ix ON ix.indrelid = t.oid
JOIN pg_catalog.pg_class i ON i.oid = ix.indexrelid
JOIN pg_catalog.pg_am am ON am.oid = i.relam
JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid
JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON a.attnum = k.attnum
WHERE t.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
AND t.relkind = 'r' AND t.relname = $1
AND NOT ix.indisprimary
GROUP BY i.relname, ix.indisunique, am.amname
ORDER BY i.relname
"""

_TBL_COMMENT_SQL = """
SELECT d.description
FROM pg_catalog.pg_class pgc
LEFT JOIN pg_catalog.pg_description d ON d.objoid = pgc.oid AND d.objsubid = 0
WHERE pgc.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
AND pgc.relkind = 'r' AND pgc.relname = $1
"""

_ROW_COUNT_SQL = """
SELECT reltuples::bigint AS estimate
FROM pg_catalog.pg_class
WHERE relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
AND relname = $1
"""


async def handle_describe_table(table_name: str) -> dict:
    # Basic name validation — only allow safe identifiers
    if not table_name.replace("_", "").isalnum():
        return {"isError": True, "content": "Invalid table name"}
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET statement_timeout = '5000'")
            col_rows = await conn.fetch(_COLS_SQL, table_name)
            if not col_rows:
                return {"isError": True, "content": f"Table '{table_name}' not found in public schema"}
            idx_rows = await conn.fetch(_IDX_SQL, table_name)
            tbl_row = await conn.fetchrow(_TBL_COMMENT_SQL, table_name)
            count_row = await conn.fetchrow(_ROW_COUNT_SQL, table_name)
            # safe quoted identifier for sample query
            sample_rows = await conn.fetch(
                f'SELECT * FROM "{table_name}" LIMIT 5'
            )

        columns = []
        for r in col_rows:
            col: dict = {
                "column": r["column_name"],
                "type": r["data_type"],
                "nullable": r["is_nullable"] == "YES",
                "default": r["column_default"],
                "primary_key": r["is_primary_key"],
            }
            if r["fk_table"]:
                col["foreign_key"] = {"table": r["fk_table"], "column": r["fk_column"]}
            if r["column_comment"]:
                col["comment"] = r["column_comment"]
            columns.append(col)

        indexes = [
            {
                "name": r["index_name"],
                "type": r["index_type"],
                "unique": r["is_unique"],
                "columns": list(r["columns"]),
            }
            for r in idx_rows
        ]

        result = {
            "table": table_name,
            "comment": tbl_row["description"] if tbl_row else None,
            "row_count_estimate": count_row["estimate"] if count_row else None,
            "columns": columns,
            "indexes": indexes,
            "sample_rows": [dict(r) for r in sample_rows],
        }
        return {"isError": False, "content": json.dumps(result, default=str)}
    except asyncpg.PostgresError as e:
        return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}
