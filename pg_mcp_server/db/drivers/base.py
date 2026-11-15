import re
from abc import ABC, abstractmethod

_BLOCKED = [
    r'\bDROP\b', r'\bDELETE\b', r'\bUPDATE\b', r'\bINSERT\b',
    r'\bTRUNCATE\b', r'\bALTER\b', r'\bCREATE\b', r'\bGRANT\b',
    r'\bREVOKE\b', r'\bEXEC\b', r'\bEXECUTE\b', r'\bCOPY\b',
    r'--', r';.+\S',
]
_TABLE_NAME_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


class BaseDriver(ABC):
    """Engine-agnostic interface every DB driver must implement."""

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def connect(self, url: str) -> None:
        """Initialise pool / connection for this URL."""

    @abstractmethod
    async def close(self) -> None:
        """Release all connections."""

    # ── Tool handlers ─────────────────────────────────────────────────────────

    @abstractmethod
    async def run_query(self, sql: str, limit: int = 100) -> dict: ...

    @abstractmethod
    async def list_tables(self) -> dict: ...

    @abstractmethod
    async def get_row_count(self, table_name: str) -> dict: ...

    @abstractmethod
    async def describe_table(self, table_name: str) -> dict: ...

    @abstractmethod
    async def search_schema(self, query: str) -> dict: ...

    @abstractmethod
    async def explain_query(self, sql: str, analyze: bool = False) -> dict: ...

    @abstractmethod
    async def get_db_health(self) -> dict: ...

    @abstractmethod
    async def get_slow_queries(self, limit: int = 10) -> dict: ...

    @abstractmethod
    async def annotate_table(self, table_name: str, comment: str) -> dict: ...

    @abstractmethod
    async def annotate_column(
        self, table_name: str, column_name: str, comment: str
    ) -> dict: ...

    # ── Shared validation (works for any SQL engine) ──────────────────────────

    def validate_query(self, sql: str) -> tuple[bool, str | None]:
        stripped = sql.strip()
        if not stripped.upper().startswith("SELECT"):
            return False, "Query must start with SELECT"
        for pattern in _BLOCKED:
            if re.search(pattern, stripped, re.IGNORECASE):
                return False, f"Query contains blocked pattern: {pattern}"
        return True, None

    def validate_table_name(self, name: str) -> tuple[bool, str | None]:
        if not _TABLE_NAME_RE.match(name):
            return False, f"Invalid table name: {name!r}"
        return True, None

    def inject_limit(self, sql: str, limit: int) -> str:
        if not re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
            return f"{sql.rstrip().rstrip(';')} LIMIT {limit}"
        return sql

    # ── Helper for unsupported features ──────────────────────────────────────

    @staticmethod
    def _not_supported(feature: str) -> dict:
        return {
            "isError": True,
            "content": f"{feature} is not supported for this database engine.",
        }
