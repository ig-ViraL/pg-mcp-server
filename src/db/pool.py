from contextvars import ContextVar
from typing import Optional

# Set per-request by DBUrlMiddleware when X-DB-URL header is present.
_db_url_var: ContextVar[Optional[str]] = ContextVar("db_url", default=None)

# Set per-request by DBUrlMiddleware when Mcp-Session-Id header is present.
_session_id_var: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
