import argparse
import os
import sys
from typing import Annotated

from dotenv import load_dotenv

load_dotenv()  # .env in the working directory, if present

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from pg_mcp_server import __version__
from pg_mcp_server.config import load_policy, set_policy
from pg_mcp_server.db.pool import _db_url_var, _session_id_var
from pg_mcp_server.crypto import decrypt_db_url
from pg_mcp_server.tools.run_query import handle_run_query
from pg_mcp_server.tools.list_tables import handle_list_tables
from pg_mcp_server.tools.get_row_count import handle_get_row_count
from pg_mcp_server.tools.describe_table import handle_describe_table
from pg_mcp_server.tools.search_schema import handle_search_schema
from pg_mcp_server.tools.annotate import handle_annotate_table, handle_annotate_column
from pg_mcp_server.tools.explain_query import handle_explain_query
from pg_mcp_server.tools.get_db_health import handle_get_db_health
from pg_mcp_server.tools.get_slow_queries import handle_get_slow_queries

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
            headers = dict(scope.get("headers", []))
            raw = headers.get(b"x-db-url", b"")
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

                session_id = headers.get(b"mcp-session-id", b"").decode() or None
                url_token = _db_url_var.set(url)
                sid_token = _session_id_var.set(session_id)
                try:
                    await self._app(scope, receive, send)
                finally:
                    _db_url_var.reset(url_token)
                    _session_id_var.reset(sid_token)
                return
        await self._app(scope, receive, send)


_extra_hosts = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
_extra_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
_security = TransportSecuritySettings(
    allowed_hosts=["localhost", "127.0.0.1", "localhost:*", "127.0.0.1:*"] + _extra_hosts,
    allowed_origins=["http://localhost:*", "http://127.0.0.1:*"] + _extra_origins,
)

set_policy(load_policy())

app = FastMCP("pg-mcp-server", transport_security=_security)

_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_WRITE_META = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@app.tool(annotations=_READ)
async def run_query(
    sql: Annotated[str, "SELECT query to execute"],
    limit: Annotated[int, "Max rows to return (capped by the server, default 100)"] = 100,
) -> str:
    """Run one read-only SELECT (WITH/VALUES allowed). The query is parsed and checked, runs in a READ ONLY transaction with a timeout, and results are capped at the server's row limit."""
    result = await handle_run_query(sql=sql, limit=limit)
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool(annotations=_READ)
async def list_tables() -> str:
    """List all tables with columns, types, PKs, FKs, indexes, and comments."""
    result = await handle_list_tables()
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool(annotations=_READ)
async def get_row_count(
    table_name: Annotated[str, "Table name"],
) -> str:
    """Get the exact row count for a table."""
    result = await handle_get_row_count(table_name=table_name)
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool(annotations=_READ)
async def describe_table(
    table_name: Annotated[str, "Table name"],
) -> str:
    """Deep metadata for a single table: columns, PKs, FKs, indexes, sample rows, row count, and comment."""
    result = await handle_describe_table(table_name=table_name)
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool(annotations=_READ)
async def search_schema(
    query: Annotated[str, "Keywords to search (e.g. 'customer revenue')"],
) -> str:
    """Full-text search across table names, column names, and column comments."""
    result = await handle_search_schema(query=query)
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool(annotations=_WRITE_META)
async def annotate_table(
    table_name: Annotated[str, "Table name"],
    comment: Annotated[str, "Business description for this table"],
) -> str:
    """Write a business-context comment onto a PostgreSQL table (COMMENT ON TABLE)."""
    result = await handle_annotate_table(table_name=table_name, comment=comment)
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool(annotations=_WRITE_META)
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


@app.tool(annotations=_READ)
async def explain_query(
    sql: Annotated[str, "SELECT query to explain"],
    analyze: Annotated[bool, "Run ANALYZE for real execution stats (default false)"] = False,
) -> str:
    """Return the query plan as JSON. Use analyze=false (default) for safe plan inspection without execution."""
    result = await handle_explain_query(sql=sql, analyze=analyze)
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool(annotations=_READ)
async def get_slow_queries(
    limit: Annotated[int, "Number of slowest queries to return (1-100, default 10)"] = 10,
) -> str:
    """Top-N slowest queries by total execution time from pg_stat_statements."""
    result = await handle_get_slow_queries(limit=limit)
    if result.get("isError"):
        raise ValueError(result["content"])
    return result["content"]


@app.tool(annotations=_READ)
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

def http_app():
    """ASGI app for the Streamable HTTP transport (served at /mcp)."""
    from starlette.middleware.cors import CORSMiddleware

    return CORSMiddleware(
        DBUrlMiddleware(app.streamable_http_app()),
        allow_origins=_extra_origins or ["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", "Authorization", "Mcp-Session-Id", "Mcp-Protocol-Version", "X-DB-URL"],
        expose_headers=["Mcp-Session-Id"],
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="pg-mcp-server",
        description="Safe, audited PostgreSQL access for AI agents over MCP.",
    )
    parser.add_argument("--transport", choices=["stdio", "http"],
                        default=os.environ.get("PGMCP_TRANSPORT", "stdio"),
                        help="stdio for desktop clients (default), http for Streamable HTTP at /mcp")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    parser.add_argument("--database-url", help="PostgreSQL URL (otherwise DATABASE_URL)")
    parser.add_argument("--config", help="Path to a pg-mcp.toml policy file (otherwise PGMCP_CONFIG)")
    parser.add_argument("--audit-log", help='Audit log path, or "-" for stderr (otherwise PGMCP_AUDIT_LOG)')
    parser.add_argument("--version", action="version", version=f"pg-mcp-server {__version__}")
    args = parser.parse_args(argv)

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    if args.audit_log:
        os.environ["PGMCP_AUDIT_LOG"] = args.audit_log
    try:
        set_policy(load_policy(args.config))
    except (OSError, ValueError) as e:
        parser.error(f"invalid configuration: {e}")

    if args.transport == "stdio":
        if not os.environ.get("DATABASE_URL"):
            parser.error("set DATABASE_URL or pass --database-url")
        app.run("stdio")
        return

    import uvicorn

    uvicorn.run(http_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
