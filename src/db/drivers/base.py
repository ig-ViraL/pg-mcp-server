from abc import ABC, abstractmethod

from src.safety import validator


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
        return validator.validate_query(sql)

    def validate_table_name(self, name: str) -> tuple[bool, str | None]:
        return validator.validate_table_name(name)

    def inject_limit(self, sql: str, limit: int) -> str:
        return validator.inject_limit(sql, limit)

    # ── Helper for unsupported features ──────────────────────────────────────

    @staticmethod
    def _not_supported(feature: str) -> dict:
        return {
            "isError": True,
            "content": f"{feature} is not supported for this database engine.",
        }
