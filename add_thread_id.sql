-- painminer — add items.thread_id (parent thread grouping). Additive migration.
--
-- Groups items that belong to the same discussion so a single thread cannot
-- dominate a run's findings. For Hacker News this is the story id (a comment's
-- parent story, a story's own id). Nullable: sources without a thread concept
-- leave it null.

alter table items add column if not exists thread_id text;
