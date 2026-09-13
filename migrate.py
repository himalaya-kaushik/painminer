"""Apply a .sql file to the Supabase Postgres.

Migration tooling only — the application talks to Postgres through the
Supabase client (db.py), never through this. Reads SUPABASE_DB_URL from .env.

Supabase serves the *direct* connection (db.<ref>.supabase.co) over IPv6
only. On a host without IPv6 egress that times out, so this falls back to the
Supavisor *pooler* (IPv4), which the design doc recommends anyway (§12). The
pooler host is region-specific; the region is discovered by probing when the
direct connection is unreachable.

The DB URL password may contain raw '/' and '%' that a URI parser would
misread, so the connection parts are split by hand and passed to psycopg as
keyword arguments rather than as a URI.

    python migrate.py schema.sql
"""

from __future__ import annotations

import os
import sys

import psycopg
from dotenv import load_dotenv

# Regions tried when falling back to the pooler, cheapest-first-ish.
_POOLER_REGIONS = (
    "ap-south-1", "ap-southeast-1", "ap-southeast-2", "ap-northeast-1",
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
    "eu-central-1", "eu-west-1", "eu-west-2", "sa-east-1", "ca-central-1",
)
_POOLER_PREFIXES = ("aws-0", "aws-1")


def parse_db_url(url: str) -> dict[str, str | int]:
    """Split postgresql://user:pass@host:port/dbname without decoding pass."""
    if "://" not in url:
        raise ValueError("SUPABASE_DB_URL is not a postgresql:// URL")
    _, rest = url.split("://", 1)
    creds, hostpart = rest.rsplit("@", 1)          # last '@' splits creds from host
    user, password = creds.split(":", 1)           # first ':' splits user from pass
    hostport, dbname = hostpart.split("/", 1)
    dbname = dbname.split("?", 1)[0]                # drop any query string
    host, port = hostport.rsplit(":", 1)
    return {
        "user": user,
        "password": password,
        "host": host,
        "port": int(port),
        "dbname": dbname,
        "sslmode": "require",
    }


def _project_ref(host: str) -> str | None:
    """Extract <ref> from db.<ref>.supabase.co."""
    if host.startswith("db.") and host.endswith(".supabase.co"):
        return host[len("db."):-len(".supabase.co")]
    return None


def _discover_pooler(direct: dict[str, str | int]) -> dict[str, str | int]:
    """Find the working Supavisor pooler for this project by probing regions."""
    ref = _project_ref(str(direct["host"]))
    if not ref:
        raise RuntimeError(
            f"cannot derive pooler for host {direct['host']!r}; "
            "set SUPABASE_DB_URL to a reachable connection string"
        )
    pooler_user = f"postgres.{ref}"
    for prefix in _POOLER_PREFIXES:
        for region in _POOLER_REGIONS:
            host = f"{prefix}-{region}.pooler.supabase.com"
            kwargs = {
                "user": pooler_user,
                "password": direct["password"],
                "host": host,
                "port": 5432,                       # session pooler
                "dbname": direct["dbname"],
                "sslmode": "require",
            }
            try:
                psycopg.connect(**kwargs, connect_timeout=6).close()
                print(f"using pooler {host}")
                return kwargs
            except Exception:
                continue
    raise RuntimeError("no reachable Supabase pooler found across known regions")


def connect(direct: dict[str, str | int]) -> psycopg.Connection:
    """Connect directly if possible, else via the discovered pooler."""
    try:
        return psycopg.connect(**direct, connect_timeout=8)
    except psycopg.OperationalError:
        return psycopg.connect(**_discover_pooler(direct))


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python migrate.py <file.sql>")
    sql_path = sys.argv[1]

    load_dotenv()
    db_url = os.getenv("SUPABASE_DB_URL")
    if not db_url:
        sys.exit("SUPABASE_DB_URL is not set in .env")

    with open(sql_path, encoding="utf-8") as fh:
        sql = fh.read()

    with connect(parse_db_url(db_url)) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    print(f"applied {sql_path}")


if __name__ == "__main__":
    main()
