import os
import sys
from pathlib import Path
from typing import Annotated
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env")

# FastMCP reads FASTMCP_HOST / FASTMCP_PORT at instantiation time.
# Railway injects PORT, so map it here before FastMCP() is created.
os.environ.setdefault("FASTMCP_HOST", "0.0.0.0")
if "PORT" in os.environ:
    os.environ.setdefault("FASTMCP_PORT", os.environ["PORT"])

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp.server.fastmcp import FastMCP
from src.tools.run_query import handle_run_query
from src.tools.list_tables import handle_list_tables
from src.tools.get_row_count import handle_get_row_count

app = FastMCP("postgres-mcp")


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
    """List all tables and their columns in the public schema."""
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


if __name__ == "__main__":
    app.run(transport="sse")
