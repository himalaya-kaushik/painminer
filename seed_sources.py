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
        "metadata_fields": ["points", "num_comments"],   # engagement (build rule)
        # first non-empty wins: comment body, then Ask/Show text, then title
        "text_fields": ["comment_text", "story_text", "title"],
        "url_template": "https://news.ycombinator.com/item?id={objectID}",
        "timeout_seconds": 20,
    },
}

# GitHub Issues via the Search API — unmet-need / demand signals. Its own
# adapter file (custom search + 👍 engagement + GitHub pagination).
GITHUB = {
    "name": "github",
    "adapter": "json_api",
    "cursor_field": "updated_at",
    "enabled": True,
    "config_json": {
        "adapter_impl": "github",
        "base_url": "https://api.github.com",
        "endpoint": "search/issues",
        "queries": [
            'label:wontfix -"Dependency Dashboard" -label:dependencies',
            'label:"feature request" state:open comments:>3 -label:dependencies',
        ],
        "hits_per_page": 100,
        "hits_per_page_param": "per_page",
        "page_param": "page",
        "overlap_hours": 0,
        "throttle_seconds": 2,          # unauth search is ~10/min
        "timeout_seconds": 20,
    },
}

# Lobsters — config-only, using the generic json_api engine (top-level array,
# ISO timestamps, single page of newest).
LOBSTERS = {
    "name": "lobsters",
    "adapter": "json_api",
    "cursor_field": "created_at",
    "enabled": True,
    "config_json": {
        "adapter_impl": "json_api",
        "base_url": "https://lobste.rs",
        "endpoint": "newest.json",
        "hits_path": "",                # response is a bare array
        "hits_per_page_param": "",      # endpoint takes no paging params
        "page_param": "",
        "date_sorted": True,
        "timestamp_is_iso": True,
        "id_field": "short_id",
        "timestamp_field": "created_at",
        "text_fields": ["description_plain", "description", "title"],
        "url_template": "{comments_url}",
        "clean_html": True,
        "timeout_seconds": 20,
    },
}

# Stack Overflow via the Stack Exchange API — config-only (object with items,
# has_more paging, epoch timestamps). High-vote/new questions are unmet need.
STACKOVERFLOW = {
    "name": "stackoverflow",
    "adapter": "json_api",
    "cursor_field": "creation_date",
    "enabled": True,
    "config_json": {
        "adapter_impl": "json_api",
        "base_url": "https://api.stackexchange.com/2.3",
        "endpoint": "questions",
        "params": {"site": "stackoverflow", "order": "desc",
                   "sort": "creation", "filter": "withbody"},
        "hits_path": "items",
        "more_path": "has_more",
        "date_sorted": True,
        "page_start": 1,                # Stack Exchange pages are 1-indexed
        "hits_per_page": 100,
        "hits_per_page_param": "pagesize",
        "page_param": "page",
        "id_field": "question_id",
        "timestamp_field": "creation_date",
        "text_fields": ["body"],
        "url_template": "{link}",
        "clean_html": True,
        "max_pages": 3,                 # unauth quota is ~300/day
        "timeout_seconds": 20,
    },
}

SOURCES = [HACKER_NEWS, GITHUB, LOBSTERS, STACKOVERFLOW]


def main() -> None:
    db = DB.from_config()
    for source in SOURCES:
        written = db.upsert("sources", source, on_conflict="name")
        print(f"seeded source: {written['name']} (adapter={written['adapter']})")


if __name__ == "__main__":
    main()
