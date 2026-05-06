import json
import asyncpg
from src.db.pool import get_pool

_COLS_SQL = """
WITH pk_cols AS (
    SELECT tc.table_name, kcu.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
    WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = 'public'
),
fk_cols AS (
    SELECT kcu.table_name, kcu.column_name, ccu.table_name AS ref_table, ccu.column_name AS ref_column
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage ccu
        ON ccu.constraint_name = tc.constraint_name
    WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
),
col_comments AS (
    SELECT pgc.relname AS table_name, a.attname AS column_name, d.description
    FROM pg_catalog.pg_class pgc
    JOIN pg_catalog.pg_attribute a ON a.attrelid = pgc.oid AND a.attnum > 0 AND NOT a.attisdropped
    LEFT JOIN pg_catalog.pg_description d ON d.objoid = pgc.oid AND d.objsubid = a.attnum
    WHERE pgc.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
)
SELECT
    c.table_name,
    c.column_name,
    c.data_type,
    c.is_nullable,
    c.column_default,
    (pk.column_name IS NOT NULL) AS is_primary_key,
    fk.ref_table AS fk_table,
    fk.ref_column AS fk_column,
    cc.description AS column_comment
FROM information_schema.columns c
LEFT JOIN pk_cols pk ON pk.table_name = c.table_name AND pk.column_name = c.column_name
LEFT JOIN fk_cols fk ON fk.table_name = c.table_name AND fk.column_name = c.column_name
LEFT JOIN col_comments cc ON cc.table_name = c.table_name AND cc.column_name = c.column_name
WHERE c.table_schema = 'public'
ORDER BY c.table_name, c.ordinal_position
"""

_IDX_SQL = """
SELECT
    t.relname AS table_name,
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
AND t.relkind = 'r'
AND NOT ix.indisprimary
GROUP BY t.relname, i.relname, ix.indisunique, am.amname
ORDER BY t.relname, i.relname
"""

_TBL_COMMENT_SQL = """
SELECT pgc.relname AS table_name, d.description
FROM pg_catalog.pg_class pgc
LEFT JOIN pg_catalog.pg_description d ON d.objoid = pgc.oid AND d.objsubid = 0
WHERE pgc.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
AND pgc.relkind = 'r'
"""


async def handle_list_tables() -> dict:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET statement_timeout = '5000'")
            col_rows = await conn.fetch(_COLS_SQL)
            idx_rows = await conn.fetch(_IDX_SQL)
            tbl_rows = await conn.fetch(_TBL_COMMENT_SQL)

        # table comments
        tbl_comments = {r["table_name"]: r["description"] for r in tbl_rows}

        # indexes per table
        indexes: dict[str, list] = {}
        for r in idx_rows:
            indexes.setdefault(r["table_name"], []).append({
                "name": r["index_name"],
                "type": r["index_type"],
                "unique": r["is_unique"],
                "columns": list(r["columns"]),
            })

        # columns
        schema: dict[str, dict] = {}
        for r in col_rows:
            tbl = r["table_name"]
            if tbl not in schema:
                schema[tbl] = {
                    "comment": tbl_comments.get(tbl),
                    "columns": [],
                    "indexes": indexes.get(tbl, []),
                }
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
            schema[tbl]["columns"].append(col)

        return {"isError": False, "content": json.dumps(schema)}
    except asyncpg.PostgresError as e:
        return {"isError": True, "content": f"Database error: {e.sqlstate} — {e.args[0]}"}
