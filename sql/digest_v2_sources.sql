-- digest v2 — broaden sources beyond AI-agent-heavy arXiv/HN/GH inputs.
--
-- Paste into the Supabase SQL editor and run. Idempotent: the arxiv UPDATE
-- is a plain column overwrite (safe to re-run), and the INSERTs use
-- `on conflict (name) do nothing` so re-running never duplicates or
-- clobbers a row that has since accrued a real watermark.
--
-- Cursor-seeding note (see painminer/pipeline/fetch.py `_watermark_epoch`):
-- a source with `last_successful_fetch_at` null gets a 30-DAY backfill
-- window on its first fetch. That's fine for arxiv/producthunt (low
-- volume, always-fresh), but every new blog/news feed below has months to
-- years of back catalogue, so each INSERT explicitly seeds
-- `last_successful_fetch_at = now() - interval '2 days'` to bound the
-- first fetch to "the last couple of days," not "the whole archive."
--
-- Multi-feed rows: atom.py `iter_items` skips a feed_url that fails and
-- keeps the rest; the source only fails when every feed_url fails. Blogs
-- are grouped one row per kind so the synthesis 35%-share cap is sensible.

-- ---------------------------------------------------------------------------
-- 1. arxiv: add cs.DC, cs.CR, cs.SE (30 each), cs.CV (25 — very high volume)
--    Existing cs.LG/cs.CL/cs.AI (50 each) untouched. Cross-listed papers
--    dedupe for free: id_field is the Atom <id> (arXiv's abs URL, the same
--    string regardless of which category query returned the entry), and
--    `items` has `unique (source, source_id)` with upsert
--    ON CONFLICT DO NOTHING (painminer/pipeline/fetch.py), so a paper
--    cross-listed in e.g. cs.LG and cs.CV is fetched twice but inserted once.
-- ---------------------------------------------------------------------------
update sources
set config_json = jsonb_set(
    config_json,
    '{feed_urls}',
    '[
        "https://export.arxiv.org/api/query?search_query=cat:cs.LG&sortBy=submittedDate&sortOrder=descending&max_results=50",
        "https://export.arxiv.org/api/query?search_query=cat:cs.CL&sortBy=submittedDate&sortOrder=descending&max_results=50",
        "https://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results=50",
        "https://export.arxiv.org/api/query?search_query=cat:cs.DC&sortBy=submittedDate&sortOrder=descending&max_results=30",
        "https://export.arxiv.org/api/query?search_query=cat:cs.CR&sortBy=submittedDate&sortOrder=descending&max_results=30",
        "https://export.arxiv.org/api/query?search_query=cat:cs.SE&sortBy=submittedDate&sortOrder=descending&max_results=30",
        "https://export.arxiv.org/api/query?search_query=cat:cs.CV&sortBy=submittedDate&sortOrder=descending&max_results=25"
    ]'::jsonb
)
where name = 'arxiv';

-- ---------------------------------------------------------------------------
-- 2. New feed sources, one row per logical group.
-- ---------------------------------------------------------------------------

-- lab_blogs: OpenAI, Google DeepMind, Google Research, Hugging Face,
-- Databricks, PyTorch, Together AI, and Meta Engineering (ai.meta.com has
-- no discoverable RSS/Atom feed of its own; engineering.fb.com/feed/ is
-- Meta's real engineering blog and does carry AI/infra posts, so it stands
-- in for "Meta AI blog"). Anthropic has no official RSS feed anywhere on
-- anthropic.com (checked /rss.xml, /news/rss.xml, /index.xml, /feed, and
-- the /engineering page for a <link rel=alternate> — all 404 or absent) —
-- dropped per the task's own instruction to drop it if none exists.
insert into sources (name, adapter, config_json, cursor_field, last_successful_fetch_at, enabled)
values (
    'lab_blogs',
    'rss',
    jsonb_build_object(
        'adapter_impl', 'atom',
        'feed_urls', jsonb_build_array(
            'https://openai.com/news/rss.xml',
            'https://deepmind.google/blog/rss.xml',
            'https://engineering.fb.com/feed/',
            'https://huggingface.co/blog/feed.xml',
            'https://www.databricks.com/feed',
            'https://pytorch.org/blog/feed.xml',
            'https://www.together.ai/blog/rss.xml',
            'https://research.google/blog/rss/'
        ),
        'id_field', 'id',
        'url_field', 'link',
        'clean_html', true,
        'text_fields', jsonb_build_array('title', 'summary'),
        'overlap_hours', 6,
        'timeout_seconds', 20
    ),
    'published',
    now() - interval '2 days',
    true
)
on conflict (name) do nothing;

-- research_blogs: independent researcher/practitioner blogs.
-- Simon Willison uses the `ai` tag Atom feed (simonwillison.net/tags/ai.atom),
-- not the "everything" firehose (which also carries non-AI link-blog
-- entries and is much higher volume) — this is the "tag feed if one fits"
-- case from the task.
insert into sources (name, adapter, config_json, cursor_field, last_successful_fetch_at, enabled)
values (
    'research_blogs',
    'rss',
    jsonb_build_object(
        'adapter_impl', 'atom',
        'feed_urls', jsonb_build_array(
            'https://lilianweng.github.io/index.xml',
            'https://magazine.sebastianraschka.com/feed',
            'https://huyenchip.com/feed.xml',
            'https://www.interconnects.ai/feed',
            'https://simonwillison.net/tags/ai.atom'
        ),
        'id_field', 'id',
        'url_field', 'link',
        'clean_html', true,
        'text_fields', jsonb_build_array('title', 'summary'),
        'overlap_hours', 6,
        'timeout_seconds', 20
    ),
    'published',
    now() - interval '2 days',
    true
)
on conflict (name) do nothing;

-- systems_blogs: distributed-systems practitioner blogs.
-- Jepsen dropped: jepsen.io has no RSS/Atom feed at any of the plausible
-- paths (/feed.xml, /atom.xml, /rss.xml, /blog/feed.xml,
-- /analyses/feed.xml all 404; no <link rel=alternate> on the homepage or
-- /analyses either), so there is no feed to point at.
insert into sources (name, adapter, config_json, cursor_field, last_successful_fetch_at, enabled)
values (
    'systems_blogs',
    'rss',
    jsonb_build_object(
        'adapter_impl', 'atom',
        'feed_urls', jsonb_build_array(
            'https://muratbuffalo.blogspot.com/feeds/posts/default',
            'https://brooker.co.za/blog/rss.xml'
        ),
        'id_field', 'id',
        'url_field', 'link',
        'clean_html', true,
        'text_fields', jsonb_build_array('title', 'summary'),
        'overlap_hours', 6,
        'timeout_seconds', 20
    ),
    'published',
    now() - interval '2 days',
    true
)
on conflict (name) do nothing;

-- startup_news: TechCrunch's dedicated AI and Startups category feeds
-- (not techcrunch.com's noisy main feed), Crunchbase News, Inc42 (Indian
-- startups), and the Y Combinator blog.
insert into sources (name, adapter, config_json, cursor_field, last_successful_fetch_at, enabled)
values (
    'startup_news',
    'rss',
    jsonb_build_object(
        'adapter_impl', 'atom',
        'feed_urls', jsonb_build_array(
            'https://techcrunch.com/category/artificial-intelligence/feed/',
            'https://techcrunch.com/category/startups/feed/',
            'https://news.crunchbase.com/feed/',
            'https://inc42.com/feed/',
            'https://www.ycombinator.com/blog/rss'
        ),
        'id_field', 'id',
        'url_field', 'link',
        'clean_html', true,
        'text_fields', jsonb_build_array('title', 'summary'),
        'overlap_hours', 6,
        'timeout_seconds', 20
    ),
    'published',
    now() - interval '2 days',
    true
)
on conflict (name) do nothing;
