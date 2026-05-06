import asyncpg
import os
from contextvars import ContextVar
from typing import Optional

# Set per-request by DBUrlMiddleware when X-DB-URL header is present.
_db_url_var: ContextVar[Optional[str]] = ContextVar("db_url", default=None)

# One pool per unique connection URL so user pools are reused across calls.
_pools: dict[str, asyncpg.Pool] = {}


async def get_pool() -> asyncpg.Pool:
    url = _db_url_var.get() or os.environ["DATABASE_URL"]
    if url not in _pools:
        _pools[url] = await asyncpg.create_pool(
            url,
            min_size=1,
            max_size=5,
            command_timeout=10,
        )
    return _pools[url]


async def close_pool() -> None:
    for pool in _pools.values():
        await pool.close()
    _pools.clear()
