"""migrate.parse_db_url — must not mangle passwords with raw '/' and '%'."""

from migrate import parse_db_url, _project_ref


def test_parses_tricky_password_verbatim():
    url = "postgresql://postgres:wJ/f2N%NJ%Nzm_h@db.abc123.supabase.co:5432/postgres"
    parts = parse_db_url(url)
    assert parts["user"] == "postgres"
    assert parts["password"] == "wJ/f2N%NJ%Nzm_h"   # not URL-decoded
    assert parts["host"] == "db.abc123.supabase.co"
    assert parts["port"] == 5432
    assert parts["dbname"] == "postgres"
    assert parts["sslmode"] == "require"


def test_drops_query_string_from_dbname():
    url = "postgresql://u:p@h.example.com:6543/postgres?sslmode=require"
    assert parse_db_url(url)["dbname"] == "postgres"


def test_rejects_non_postgres_url():
    try:
        parse_db_url("mysql://u:p@h:3306/db")
    except ValueError:
        return
    raise AssertionError("expected ValueError for non-postgresql URL")


def test_project_ref_extraction():
    assert _project_ref("db.zvuqtfkzdlilfcgrbdcz.supabase.co") == "zvuqtfkzdlilfcgrbdcz"
    assert _project_ref("aws-0-ap-southeast-1.pooler.supabase.com") is None
