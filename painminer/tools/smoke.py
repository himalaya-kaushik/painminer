"""Phase 1 smoke test: insert one row and read it back.

Verifies that .env loads, the Supabase client connects, and schema.sql ran.
Inserts a disabled source named 'smoke_test', reads it back, prints it, then
deletes it so the run is idempotent and leaves the registry clean.

    .venv/bin/python -m painminer.tools.smoke
"""

from __future__ import annotations

from painminer.db import DB

SMOKE_SOURCE = "smoke_test"


def main() -> None:
    db = DB.from_config()

    # Upsert so a re-run after a failed cleanup does not hit the unique key.
    written = db.upsert(
        "sources",
        {
            "name": SMOKE_SOURCE,
            "adapter": "json_api",
            "config_json": {"note": "phase 1 smoke test"},
            "enabled": False,
        },
        on_conflict="name",
    )
    print("inserted:", written)

    read_back = db.get("sources", name=SMOKE_SOURCE)
    print("read back:", read_back)

    assert read_back is not None, "row did not read back"
    assert read_back["name"] == SMOKE_SOURCE
    assert read_back["adapter"] == "json_api"

    db.table("sources").delete().eq("name", SMOKE_SOURCE).execute()
    print("cleaned up:", SMOKE_SOURCE)
    print("OK — insert and read-back succeeded.")


if __name__ == "__main__":
    main()
