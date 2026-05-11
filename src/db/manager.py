import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

from .pool import _db_url_var, _session_id_var
from .drivers.base import BaseDriver
from .drivers.postgres import PostgresDriver
from .drivers.mysql import MySQLDriver
from .drivers.sqlite import SQLiteDriver

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables (all overridable via env vars)
# ---------------------------------------------------------------------------

# Max concurrent cached sessions. When hit, LRU session is evicted first.
_MAX_SESSIONS: int = int(os.environ.get("MCP_MAX_SESSIONS", "50"))

# Seconds of inactivity after which a session's pool is closed.
_IDLE_TTL_SECS: float = float(os.environ.get("MCP_IDLE_TTL_SECS", str(30 * 60)))

# How often the background task wakes up to check for stale sessions.
# Defaults to 1 minute — sessions are closed within 1 min of going stale.
_EVICTION_INTERVAL_SECS: float = float(os.environ.get("MCP_EVICTION_INTERVAL_SECS", "60"))

# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------

@dataclass
class _Entry:
    driver: BaseDriver
    url: str
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def is_stale(self) -> bool:
        return (time.monotonic() - self.last_used) > _IDLE_TTL_SECS

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_cache: dict[str, _Entry] = {}
_lock: asyncio.Lock | None = None          # lazy-init — event loop must be running
_eviction_task: asyncio.Task | None = None # background cleanup task


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _make_driver(url: str) -> BaseDriver:
    if url.startswith(("postgresql://", "postgres://")):
        return PostgresDriver()
    if url.startswith(("mysql://", "mariadb://", "mysql+asyncmy://")):
        return MySQLDriver()
    if url.startswith(("sqlite:///", "sqlite+aiosqlite:///")):
        return SQLiteDriver()
    raise ValueError(
        "Unsupported database URL scheme. "
        "Supported prefixes: postgresql://, mysql://, mariadb://, sqlite:///"
    )

# ---------------------------------------------------------------------------
# Background eviction loop
# ---------------------------------------------------------------------------

async def _eviction_loop() -> None:
    """
    Runs every _EVICTION_INTERVAL_SECS and closes any session that has been
    idle longer than _IDLE_TTL_SECS. Completely independent of incoming requests.
    """
    while True:
        await asyncio.sleep(_EVICTION_INTERVAL_SECS)
        async with _get_lock():
            stale = [k for k, e in _cache.items() if e.is_stale()]
            for key in stale:
                entry = _cache.pop(key)
                try:
                    await entry.driver.close()
                except Exception:
                    pass
                logger.info(
                    "session evicted (idle %.0fs > TTL %.0fs): key=%s url=%.40s...",
                    time.monotonic() - entry.last_used,
                    _IDLE_TTL_SECS,
                    key,
                    entry.url,
                )


def _ensure_eviction_loop() -> None:
    """Start the background eviction task if it isn't already running."""
    global _eviction_task
    if _eviction_task is None or _eviction_task.done():
        _eviction_task = asyncio.create_task(_eviction_loop())
        _eviction_task.set_name("mcp-session-eviction")

# ---------------------------------------------------------------------------
# Eviction helpers (must be called while holding the lock)
# ---------------------------------------------------------------------------

async def _evict_lru() -> None:
    """Close and drop the single least-recently-used session."""
    if not _cache:
        return
    lru_key = min(_cache, key=lambda k: _cache[k].last_used)
    entry = _cache.pop(lru_key)
    try:
        await entry.driver.close()
    except Exception:
        pass
    logger.info("session evicted (LRU cap=%d): key=%s url=%.40s...", _MAX_SESSIONS, lru_key, entry.url)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_driver() -> BaseDriver:
    """
    Return the cached driver for the current request, starting the background
    eviction loop on the first call if it isn't already running.

    Cache key precedence:
      1. Mcp-Session-Id  (BYOD — one pool per MCP session)
      2. Raw URL          (BYOD fallback for clients without a session ID)
      3. "default"        (no X-DB-URL — uses DATABASE_URL, shared pool)
    """
    _ensure_eviction_loop()

    byod_url = _db_url_var.get()
    session_id = _session_id_var.get()
    url = byod_url or os.environ.get("DATABASE_URL", "")

    if not url:
        raise ValueError("No database URL configured (set DATABASE_URL or send X-DB-URL header)")

    cache_key = (
        f"session:{session_id}" if (byod_url and session_id)
        else f"url:{url}" if byod_url
        else "default"
    )

    async with _get_lock():
        if cache_key in _cache:
            _cache[cache_key].touch()
            return _cache[cache_key].driver

        # At capacity — evict LRU before creating a new pool
        if len(_cache) >= _MAX_SESSIONS:
            await _evict_lru()

        driver = _make_driver(url)
        await driver.connect(url)
        _cache[cache_key] = _Entry(driver=driver, url=url)
        logger.info("session created: key=%s url=%.40s...", cache_key, url)
        return driver


async def close_session(session_id: str) -> bool:
    """Explicitly close and evict a session by its MCP session ID. Returns True if found."""
    key = f"session:{session_id}"
    async with _get_lock():
        if key not in _cache:
            return False
        entry = _cache.pop(key)
        try:
            await entry.driver.close()
        except Exception:
            pass
        logger.info("session closed explicitly: key=%s", key)
        return True


async def close_all() -> None:
    """Cancel the eviction task and tear down every cached pool (server shutdown)."""
    global _eviction_task
    if _eviction_task and not _eviction_task.done():
        _eviction_task.cancel()
        try:
            await _eviction_task
        except asyncio.CancelledError:
            pass
        _eviction_task = None

    async with _get_lock():
        for entry in _cache.values():
            try:
                await entry.driver.close()
            except Exception:
                pass
        _cache.clear()


def pool_stats() -> dict:
    """Live snapshot of the session cache — useful for observability."""
    now = time.monotonic()
    return {
        "total_sessions": len(_cache),
        "max_sessions": _MAX_SESSIONS,
        "idle_ttl_secs": _IDLE_TTL_SECS,
        "eviction_interval_secs": _EVICTION_INTERVAL_SECS,
        "eviction_task_running": _eviction_task is not None and not _eviction_task.done(),
        "sessions": [
            {
                "key": k,
                "url_prefix": e.url[:30] + "..." if len(e.url) > 30 else e.url,
                "idle_secs": round(now - e.last_used, 1),
                "age_secs": round(now - e.created_at, 1),
            }
            for k, e in sorted(_cache.items(), key=lambda kv: kv[1].last_used, reverse=True)
        ],
    }
