-- painminer — add items.metadata (source-specific extras). Additive migration.
--
-- Holds non-text signal the judge or ranker may use without polluting
-- raw_text/content_hash — e.g. HN submission points and comment counts, used
-- by the `build` engagement rule. Nullable jsonb, default empty.

alter table items add column if not exists metadata jsonb not null default '{}'::jsonb;
