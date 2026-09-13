"""Seed the source registry (§3.2).

Idempotent: upserts registry rows by name. Adding a source later is another
entry here (or a direct row) — not code, for anything the generic engine or an
existing adapter covers.

    python seed_sources.py
"""

from __future__ import annotations

from db import DB

# Hacker News via the Algolia API. adapter='json_api' satisfies the schema's
# adapter CHECK; config_json.adapter_impl selects the HN-specific class, which
# reuses the json_api engine but windows past Algolia's 1000-result cap.
HACKER_NEWS = {
    "name": "hackernews",
    "adapter": "json_api",
    "cursor_field": "created_at_i",
    "enabled": True,
    "config_json": {
        "adapter_impl": "hackernews",
        "base_url": "https://hn.algolia.com/api/v1",
        "endpoint": "search_by_date",
        # Prioritize Ask HN / Show HN and phrase-targeted pain/build searches
        # over general comment volume. Each entry is {tags?, query?}. Tunable.
        "searches": [
            {"tags": "ask_hn"},
            {"tags": "show_hn"},
            {"tags": "comment", "query": "I built"},
            {"tags": "comment", "query": "frustrated with"},
            {"tags": "comment", "query": "wish there was"},
            {"tags": "comment", "query": "workaround"},
            {"tags": "comment", "query": "manually"},
            {"tags": "comment", "query": "there's no good"},
        ],
        "hits_per_page": 100,
        "overlap_hours": 6,
        "clean_html": True,        # HN comment_text is HTML; decode to plain text
        "id_field": "objectID",
        "timestamp_field": "created_at_i",
        "thread_id_field": "story_id",   # parent thread, for the per-thread cap
        # first non-empty wins: comment body, then Ask/Show text, then title
        "text_fields": ["comment_text", "story_text", "title"],
        "url_template": "https://news.ycombinator.com/item?id={objectID}",
        "timeout_seconds": 20,
    },
}

SOURCES = [HACKER_NEWS]


def main() -> None:
    db = DB.from_config()
    for source in SOURCES:
        written = db.upsert("sources", source, on_conflict="name")
        print(f"seeded source: {written['name']} (adapter={written['adapter']})")


if __name__ == "__main__":
    main()
