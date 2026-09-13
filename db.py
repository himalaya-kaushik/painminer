"""Thin typed wrapper over the Supabase client.

Deliberately small: it hands back the raw PostgREST query builder via
`table()` so later stages compose their own queries, and adds a few typed
convenience methods for the operations every stage needs (insert, upsert,
single-row read). It does not try to model the schema — the SQL is the source
of truth for that.

Uses the service key, so it bypasses RLS. Machine A only.
"""

from __future__ import annotations

from typing import Any

from supabase import Client, create_client

from config import Config, load_config

# The tables defined in schema.sql. Used to guard against typos in table names.
TABLES = ("sources", "items", "findings", "clusters", "feedback", "runs")

Row = dict[str, Any]


class DB:
    """A thin wrapper around a Supabase client."""

    def __init__(self, client: Client) -> None:
        self.client = client

    @classmethod
    def from_config(cls, config: Config | None = None) -> "DB":
        cfg = config or load_config()
        client = create_client(cfg.supabase_url, cfg.supabase_service_key)
        return cls(client)

    def table(self, name: str):
        """Return the raw PostgREST query builder for a table.

        Use this for anything beyond the convenience methods below.
        """
        if name not in TABLES:
            raise ValueError(f"unknown table {name!r}; expected one of {TABLES}")
        return self.client.table(name)

    def insert(self, table: str, row: Row) -> Row:
        """Insert one row and return it as written (including generated ids)."""
        resp = self.table(table).insert(row).execute()
        return resp.data[0]

    def upsert(self, table: str, row: Row, *, on_conflict: str | None = None) -> Row:
        """Insert or update one row; return it as written."""
        builder = self.table(table)
        kwargs = {"on_conflict": on_conflict} if on_conflict else {}
        resp = builder.upsert(row, **kwargs).execute()
        return resp.data[0]

    def get(self, table: str, **eq: Any) -> Row | None:
        """Return the first row matching the given equality filters, or None."""
        builder = self.table(table).select("*")
        for column, value in eq.items():
            builder = builder.eq(column, value)
        resp = builder.limit(1).execute()
        return resp.data[0] if resp.data else None
