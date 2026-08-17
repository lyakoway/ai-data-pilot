"""ClickHouse engine wrapper — mimics the SQLAlchemy engine interface.

Wraps clickhouse-connect's HTTP client so that ``run_sql()`` and other
SQLAlchemy-expecting code work without changes: ``engine.connect()`` returns
a context manager with ``.execute(query)`` → result with ``.keys()`` and
``.fetchall()``.
"""
from __future__ import annotations

from typing import Any


class ClickHouseResult:
    """Wraps a clickhouse-connect result to mimic SQLAlchemy's Result."""

    def __init__(self, column_names: list[str], result_rows: list[tuple]) -> None:
        self._columns = column_names
        self._rows = result_rows

    def keys(self) -> list[str]:
        return list(self._columns)

    def fetchall(self) -> list[tuple]:
        return self._rows


class ClickHouseConnection:
    """Mimics SQLAlchemy Connection (context manager protocol)."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def __enter__(self) -> "ClickHouseConnection":
        return self

    def __exit__(self, *args: Any) -> None:
        pass  # HTTP client is stateless per-query

    def execute(self, query: Any) -> ClickHouseResult:
        # Handle SQLAlchemy text() objects and raw strings
        if hasattr(query, "text"):
            sql = str(query.text)
        else:
            sql = str(query)
        result = self._client.query(sql)
        return ClickHouseResult(
            list(result.column_names or []),
            [tuple(row) for row in (result.result_rows or [])],
        )


class ClickHouseEngine:
    """Mimics SQLAlchemy Engine for ClickHouse via clickhouse-connect."""

    def __init__(self, host: str, port: int, database: str, username: str, password: str,
                 secure: bool = True) -> None:
        import clickhouse_connect
        self._client = clickhouse_connect.get_client(
            host=host, port=port, username=username, password=password,
            database=database, secure=secure,
        )
        # Cache the URL for dialect detection
        self.url = f"clickhouse://{username}@{host}:{port}/{database}"

    def connect(self) -> ClickHouseConnection:
        return ClickHouseConnection(self._client)

    def dispose(self) -> None:
        if hasattr(self._client, "close"):
            self._client.close()
