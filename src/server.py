import os
import sys
from pathlib import Path
from typing import Annotated
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env")

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from src.db.pool import _db_url_var
from src.crypto import decrypt_db_url
from src.tools.run_query import handle_run_query
from src.tools.list_tables import handle_list_tables
from src.tools.get_row_count import handle_get_row_count
from src.tools.describe_table import handle_describe_table
from src.tools.search_schema import handle_search_schema
from src.tools.annotate import handle_annotate_table, handle_annotate_column
from src.tools.explain_query import handle_explain_query
from src.tools.get_db_health import handle_get_db_health
from src.tools.get_slow_queries import handle_get_slow_queries

_SHARED_SECRET = os.environ.get("SHARED_SECRET", "")

# ---------------------------------------------------------------------------
# ASGI middleware: decrypt X-DB-URL header and bind it to the request context
# ---------------------------------------------------------------------------

class DBUrlMiddleware:
    """Pure-ASGI middleware that reads X-DB-URL, decrypts it, and sets the
    per-request context variable so get_pool() uses the caller's database."""

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and _SHARED_SECRET:
            raw = dict(scope.get("headers", [])).get(b"x-db-url", b"")
            if raw:
                try:
                    url = decrypt_db_url(raw.decode(), _SHARED_SECRET)
                except Exception:
                    await send({
                        "type": "http.response.start",
                        "status": 400,
                        "headers": [(b"content-type", b"text/plain")],
                    })
                    await send({"type": "http.response.body", "body": b"Invalid X-DB-URL"})
                    return
                token = _db_url_var.set(url)
                try:
                    await self._app(scope, receive, send)
                finally:
                    _db_url_var.reset(token)
                return
        await self._app(scope, receive, send)


_extra_hosts = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
_extra_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
_security = TransportSecuritySettings(
    allowed_hosts=["localhost", "127.0.0.1"] + _extra_hosts,
    allowed_origins=["http://localhost:3000", "http://localhost:8000"] + _extra_origins,
)

app = FastMCP("postgres-mcp", transport_security=_security)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@app.tool()
async def run_query(
    sql: Annotated[str, "SELECT query to execute"],
    limit: Annotated[int, "Max rows (1-500, default 100)"] = 100,
) -> str:
    """Run a read-only SELECT query against the PostgreSQL database."""
    result = await handle_run_query(sql=sql, limit=limit)
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool()
async def list_tables() -> str:
    """List all tables with columns, types, PKs, FKs, indexes, and comments."""
    result = await handle_list_tables()
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool()
async def get_row_count(
    table_name: Annotated[str, "Table name"],
) -> str:
    """Get the exact row count for a table."""
    result = await handle_get_row_count(table_name=table_name)
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool()
async def describe_table(
    table_name: Annotated[str, "Table name"],
) -> str:
    """Deep metadata for a single table: columns, PKs, FKs, indexes, sample rows, row count, and comment."""
    result = await handle_describe_table(table_name=table_name)
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool()
async def search_schema(
    query: Annotated[str, "Keywords to search (e.g. 'customer revenue')"],
) -> str:
    """Full-text search across table names, column names, and column comments."""
    result = await handle_search_schema(query=query)
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool()
async def annotate_table(
    table_name: Annotated[str, "Table name"],
    comment: Annotated[str, "Business description for this table"],
) -> str:
    """Write a business-context comment onto a PostgreSQL table (COMMENT ON TABLE)."""
    result = await handle_annotate_table(table_name=table_name, comment=comment)
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool()
async def annotate_column(
    table_name: Annotated[str, "Table name"],
    column_name: Annotated[str, "Column name"],
    comment: Annotated[str, "Business description for this column"],
) -> str:
    """Write a business-context comment onto a PostgreSQL column (COMMENT ON COLUMN)."""
    result = await handle_annotate_column(
        table_name=table_name, column_name=column_name, comment=comment
    )
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool()
async def explain_query(
    sql: Annotated[str, "SELECT query to explain"],
    analyze: Annotated[bool, "Run ANALYZE for real execution stats (default false)"] = False,
) -> str:
    """Return the query plan as JSON. Use analyze=false (default) for safe plan inspection without execution."""
    result = await handle_explain_query(sql=sql, analyze=analyze)
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool()
async def get_slow_queries(
    limit: Annotated[int, "Number of slowest queries to return (1-100, default 10)"] = 10,
) -> str:
    """Top-N slowest queries by total execution time from pg_stat_statements."""
    result = await handle_get_slow_queries(limit=limit)
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool()
async def get_db_health() -> str:
    """Health check: unused indexes, table bloat, long-running queries, and connection utilization."""
    result = await handle_get_db_health()
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


# ---------------------------------------------------------------------------
# Prompt resource
# ---------------------------------------------------------------------------

@app.prompt()
async def nl_to_sql() -> str:
    """Reusable system prompt for generating safe, high-quality PostgreSQL SELECT queries."""
    return """You are a PostgreSQL expert generating read-only SELECT queries.

Rules:
- NEVER use SELECT * — select only the columns needed
- ALWAYS alias tables in JOINs (e.g. customers c, orders o)
- ALWAYS add LIMIT for exploratory queries (default 100)
- Call describe_table before writing any JOIN query
- If the question is ambiguous, ask ONE clarifying question instead of guessing
- When you make an assumption, state it explicitly before the SQL
- Prefer window functions over nested subqueries when equivalent
- Use date_trunc() for time bucketing, not EXTRACT()

Chain-of-thought before SQL:
1. Identify the relevant tables
2. Identify the JOIN path using foreign keys
3. Identify filters and aggregations needed
4. Write the SQL"""


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware

    port = int(os.environ.get("PORT", 8000))
    mcp_app = CORSMiddleware(
        DBUrlMiddleware(app.streamable_http_app()),
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", "Mcp-Session-Id", "X-DB-URL"],
        expose_headers=["Mcp-Session-Id"],
    )
    uvicorn.run(mcp_app, host="0.0.0.0", port=port)
