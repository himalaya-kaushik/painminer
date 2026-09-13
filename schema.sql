-- painminer — schema v2 (§6 of painminer-design-v2.md)
--
-- Paste this whole file into the Supabase SQL editor and run it.
-- It is idempotent: re-running it will not error and will not drop data.
--
-- Assumes the pgvector extension (>= 0.8.6) is already enabled.
-- If it is not, uncomment the line below.
-- create extension if not exists vector;

-- ---------------------------------------------------------------------------
-- sources  — the registry (§3.1, §6)
-- Adding a source is a row here, not code.
-- ---------------------------------------------------------------------------
create table if not exists sources (
    name                      text primary key,
    adapter                   text not null
        check (adapter in ('json_api', 'rss', 'crawl', 'bulk')),
    config_json               jsonb not null default '{}'::jsonb,
    cursor_field              text,                 -- e.g. created_at (HN), updated_at (GH)
    last_successful_fetch_at  timestamptz,          -- advanced only after judge succeeds (§5.1)
    enabled                   boolean not null default true,
    created_at                timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- items  — one row per fetched piece of text (§6)
-- raw_text is nulled in the same transaction as judge success.
-- ---------------------------------------------------------------------------
create table if not exists items (
    id            bigint generated always as identity primary key,
    source        text not null references sources (name),
    source_id     text not null,                    -- the source's own id for this item
    url           text,
    raw_text      text,                             -- nulled on judge success (§6)
    content_hash  text,                             -- for cross-source structural dedupe (§4.1)
    state         text not null default 'fetched'
        check (state in ('fetched', 'ready', 'duplicate', 'processing', 'done', 'failed')),
    attempts      integer not null default 0,       -- three strikes -> failed (§4.2)
    processing_at timestamptz,                       -- claim timestamp; rows stuck >1h reclaimed (§4.2)
    fetched_at    timestamptz not null default now(),
    unique (source, source_id)                       -- already seen, skip (§4.1)
);

create index if not exists items_state_idx        on items (state);
create index if not exists items_content_hash_idx on items (content_hash);
create index if not exists items_source_idx       on items (source);

-- ---------------------------------------------------------------------------
-- clusters  — recurring findings grouped by centroid (§6, §8)
-- sources_seen and days_seen are SETS, not counters (§6).
-- Centroid is anchored on the first member; it is NOT a running mean (§6).
-- ---------------------------------------------------------------------------
create table if not exists clusters (
    id                  bigint generated always as identity primary key,
    kind                text not null,               -- open taxonomy (§2)
    canonical_statement text not null,
    centroid            halfvec(384),                -- anchored on first member (§6)
    sources_seen        text[] not null default '{}',-- SET of source names (§6)
    days_seen           date[] not null default '{}',-- SET of dates (§6)
    mention_count       integer not null default 0,
    first_seen          timestamptz not null default now(),
    last_seen           timestamptz not null default now(),
    score               double precision,
    score_version       text,                        -- written on every run (§9)
    muted               boolean not null default false
);

create index if not exists clusters_kind_idx  on clusters (kind);
create index if not exists clusters_muted_idx on clusters (muted);
-- Cosine ANN over cluster centroids of the same kind (§8).
create index if not exists clusters_centroid_idx
    on clusters using hnsw (centroid halfvec_cosine_ops);

-- ---------------------------------------------------------------------------
-- findings  — one row per thing the model surfaced (§6)
-- An item yields zero, one, or many. No author field (§6).
-- kind is open taxonomy: no enum, new values accepted at write time (§2).
-- ---------------------------------------------------------------------------
create table if not exists findings (
    id             bigint generated always as identity primary key,
    item_id        bigint not null references items (id) on delete cascade,
    kind           text not null,                    -- open taxonomy (§2)
    statement      text not null,                    -- this is what gets embedded/clustered (§7.2)
    why_it_matters text,
    domain         text,
    evidence_quote text,                             -- verbatim from source (§7.2)
    confidence     double precision
        check (confidence is null or (confidence >= 0 and confidence <= 1)),
    embedding      halfvec(384),                     -- filled at embed stage; clustering only (§8)
    cluster_id     bigint references clusters (id) on delete set null,
    created_at     timestamptz not null default now()
);

create index if not exists findings_item_idx    on findings (item_id);
create index if not exists findings_cluster_idx on findings (cluster_id);
create index if not exists findings_kind_idx    on findings (kind);
-- Cosine ANN over finding embeddings (§8).
create index if not exists findings_embedding_idx
    on findings using hnsw (embedding halfvec_cosine_ops);

-- ---------------------------------------------------------------------------
-- feedback  — append-only training labels (§6, §9.2)
-- ---------------------------------------------------------------------------
create table if not exists feedback (
    id            bigint generated always as identity primary key,
    cluster_id    bigint not null references clusters (id) on delete cascade,
    verdict       text not null
        check (verdict in ('interesting', 'kill', 'watch')),
    features_json jsonb not null default '{}'::jsonb,-- stable feature set (§9.2)
    score_version text,
    created_at    timestamptz not null default now()
);

create index if not exists feedback_cluster_idx on feedback (cluster_id);

-- ---------------------------------------------------------------------------
-- runs  — one row per /scan; the observability backbone (§6, §11)
-- ---------------------------------------------------------------------------
create table if not exists runs (
    id               bigint generated always as identity primary key,
    started_at       timestamptz not null default now(),
    finished_at      timestamptz,
    items_fetched    integer not null default 0,
    findings_created integer not null default 0,
    errors           jsonb not null default '[]'::jsonb  -- list of error messages (§11)
);
