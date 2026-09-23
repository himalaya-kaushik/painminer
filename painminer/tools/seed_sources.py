"""Seed the source registry (§3.2).

Idempotent: upserts registry rows by name. Adding a source later is another
entry here (or a direct row) — not code, for anything the generic engine or an
existing adapter covers.

    .venv/bin/python -m painminer.tools.seed_sources
"""

from __future__ import annotations

from painminer.db import DB

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
            # High-engagement front-page stories (launches, releases, big
            # results) — everything else here is Ask/Show HN or a comment
            # phrase search, so a major story submission (e.g. a big model
            # launch) was previously never fetched at all. min_points is a
            # server-side floor via numericFilters, keeps volume sane.
            {"tags": "story", "min_points": 300},
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
        # Scoped to ML / infra / agent / dev-tools orgs (v3 brief §5). GitHub's
        # issues-search has no topic: qualifier, so we scope by an org allowlist
        # (multiple org: are OR'd). All three verified live to return 200 with
        # on-profile results. Add/remove orgs here — config, not code.
        "queries": [
            # ML frameworks, training, inference/serving
            'org:huggingface org:pytorch org:vllm-project org:ggml-org '
            'org:ray-project is:issue is:open label:"feature request" comments:>3',
            # agents, retrieval, LLM-app dev-tools
            'org:langchain-ai org:run-llama org:pydantic org:BerriAI '
            'org:sgl-project is:issue is:open label:"feature request" comments:>3',
            # unmet needs maintainers won't fix (demand signal)
            'org:huggingface org:vllm-project org:pytorch org:langchain-ai '
            'org:ggml-org label:wontfix',
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

# Stack Overflow — DROPPED (v3 brief §5): 1,304 questions in July 2026 vs
# 207,000 at its 2014 peak, effectively dead. Kept here with enabled=False so
# re-seeding disables the existing DB row (rather than silently leaving it on).
STACKOVERFLOW = {
    "name": "stackoverflow",
    "adapter": "json_api",
    "cursor_field": "creation_date",
    "enabled": False,
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

# arXiv via the Atom API (v3 brief §5). The `research` kind finally has a real
# source. Three cs categories as separate feeds (the AtomAdapter yields one page
# per feed). MUST be https:// — the shared fetch client does not follow the
# http->https redirect. adapter='rss' satisfies the schema; adapter_impl='atom'.
ARXIV = {
    "name": "arxiv",
    "adapter": "rss",
    "cursor_field": "published",
    "enabled": True,
    "config_json": {
        "adapter_impl": "atom",
        "feed_urls": [
            "https://export.arxiv.org/api/query?search_query=cat:cs.LG"
            "&sortBy=submittedDate&sortOrder=descending&max_results=50",
            "https://export.arxiv.org/api/query?search_query=cat:cs.CL"
            "&sortBy=submittedDate&sortOrder=descending&max_results=50",
            "https://export.arxiv.org/api/query?search_query=cat:cs.AI"
            "&sortBy=submittedDate&sortOrder=descending&max_results=50",
        ],
        "id_field": "id",
        "url_field": "link",
        "text_fields": ["title", "summary"],
        "overlap_hours": 6,
        "clean_html": True,
        "metadata_fields": ["authors", "categories"],
        "timeout_seconds": 20,
    },
}

# Product Hunt via its public Atom feed (v3 brief §5). /feed is the only
# anonymous surface; the rest is behind Cloudflare. It is Atom despite the name.
PRODUCT_HUNT = {
    "name": "producthunt",
    "adapter": "rss",
    "cursor_field": "published",
    "enabled": True,
    "config_json": {
        "adapter_impl": "atom",
        "feed_url": "https://www.producthunt.com/feed",
        "id_field": "id",
        "url_field": "link",
        "text_fields": ["title", "summary"],
        "overlap_hours": 6,
        "clean_html": True,
        "timeout_seconds": 20,
    },
}

# Hugging Face (v3 brief §5), via the public JSON API (bare arrays, no key).
# daily_papers is curated/trending with full abstracts — the strongest of the
# three. datasets carry a description when the card has one. Both verified live.
HF_PAPERS = {
    "name": "hf_papers",
    "adapter": "json_api",
    "cursor_field": "publishedAt",
    "enabled": True,
    "config_json": {
        "adapter_impl": "json_api",
        "base_url": "https://huggingface.co/api",
        "endpoint": "daily_papers",
        "params": {"limit": 50},
        "hits_path": "",                 # bare array
        "hits_per_page_param": "",       # single-page poll
        "page_param": "",
        "date_sorted": False,            # curated order, not chronological
        "timestamp_is_iso": True,
        "id_field": "title",             # no top-level id; title is unique in practice
        "timestamp_field": "publishedAt",
        "text_fields": ["summary", "title"],
        "url_template": "https://huggingface.co/papers/{paper[id]}",  # nested via str.format
        "timeout_seconds": 20,
    },
}

HF_DATASETS = {
    "name": "hf_datasets",
    "adapter": "json_api",
    "cursor_field": "createdAt",
    "enabled": True,
    "config_json": {
        "adapter_impl": "json_api",
        "base_url": "https://huggingface.co/api",
        "endpoint": "datasets",
        "params": {"sort": "createdAt", "direction": "-1", "limit": 50},
        "hits_path": "",
        "hits_per_page_param": "",
        "page_param": "",
        "date_sorted": True,
        "timestamp_is_iso": True,
        "id_field": "id",
        "timestamp_field": "createdAt",
        "text_fields": ["description", "id"],
        "url_template": "https://huggingface.co/datasets/{id}",
        "metadata_fields": ["likes", "downloads"],
        "timeout_seconds": 20,
    },
}

# HF trending MODELS: the createdAt listing is a firehose of junk fine-tunes
# with no description field (text is just the repo path), so it's pure triage
# noise. Wired but DISABLED until it can be driven by a real trending/likes
# sort. Enable by flipping this and re-seeding.
HF_MODELS = {
    "name": "hf_models",
    "adapter": "json_api",
    "cursor_field": "createdAt",
    "enabled": False,
    "config_json": {
        "adapter_impl": "json_api",
        "base_url": "https://huggingface.co/api",
        "endpoint": "models",
        "params": {"sort": "createdAt", "direction": "-1", "limit": 50},
        "hits_path": "",
        "hits_per_page_param": "",
        "page_param": "",
        "date_sorted": True,
        "timestamp_is_iso": True,
        "id_field": "id",
        "timestamp_field": "createdAt",
        "text_fields": ["id"],
        "url_template": "https://huggingface.co/{id}",
        "metadata_fields": ["likes", "downloads"],
        "timeout_seconds": 20,
    },
}

# Y Combinator Launches (v3 brief §5). www.ycombinator.com/launches serves JSON
# directly to a plain GET — no key, unlike the Algolia-backed company directory
# (whose public key rotates). High-signal founder content. Verified live.
YC_LAUNCHES = {
    "name": "yc_launches",
    "adapter": "json_api",
    "cursor_field": "created_at",
    "enabled": True,
    "config_json": {
        "adapter_impl": "json_api",
        "base_url": "https://www.ycombinator.com",
        "endpoint": "launches",
        "hits_path": "hits",
        "pages_path": "nbPages",
        "date_sorted": True,
        "hits_per_page": 50,
        "hits_per_page_param": "hitsPerPage",
        "page_param": "page",
        "id_field": "id",
        "timestamp_field": "created_at",
        "timestamp_is_iso": True,
        "text_fields": ["tagline", "title"],
        "url_template": "{search_path}",
        "timeout_seconds": 20,
    },
}

# Daily newsletters from the Gmail-fed Obsidian vault (himalaya-vault, private).
# The nightly workflow sparse-checks-out its Newsletters/ folder with a
# read-only deploy key and exports VAULT_NEWSLETTERS_DIR; if that step fails
# this source fails alone and the run carries on. Each email is split into
# stories / longform chunks (adapters/newsletter_layouts.py). adapter='bulk'
# satisfies the schema CHECK; adapter_impl selects the vault adapter.
NEWSLETTERS = {
    "name": "newsletters",
    "adapter": "bulk",
    "cursor_field": "date",
    "enabled": True,
    "config_json": {
        "adapter_impl": "newsletters",
        "vault_dir_env": "VAULT_NEWSLETTERS_DIR",
        # No per-newsletter rules: every sender in the vault is read, and
        # subscriptions can come and go with no change here. Paywall
        # cut-offs and link/footer noise are handled generically by the
        # layouts; triage filters what is off-profile. `exclude_senders`
        # (sender slugs, the filename part before "--") exists as an escape
        # hatch only.
        "exclude_senders": [],
        # Today's vault folder plus the 2 before it: the vault files mail the
        # day after it arrives, and a missed vault day is caught up next run.
        # Folders already processed re-insert nothing (deterministic ids), so
        # each night adds only what the vault newly wrote.
        "lookback_days": 2,
        "chunk_max_chars": 4000,
        "chunk_min_chars": 400,
    },
}

SOURCES = [
    HACKER_NEWS, GITHUB, LOBSTERS, STACKOVERFLOW,
    ARXIV, PRODUCT_HUNT, HF_PAPERS, HF_DATASETS, HF_MODELS, YC_LAUNCHES,
    NEWSLETTERS,
]


def main() -> None:
    db = DB.from_config()
    for source in SOURCES:
        written = db.upsert("sources", source, on_conflict="name")
        print(f"seeded source: {written['name']} (adapter={written['adapter']})")


if __name__ == "__main__":
    main()
