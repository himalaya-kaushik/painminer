-- painminer — cluster pin state (§10.3 the 👀 button). Additive migration.
--
-- 👀 pins a cluster: acknowledged and hidden from /top until its mention_count
-- moves past what it was when pinned ("resurface only when its count changes").

alter table clusters add column if not exists pinned boolean not null default false;
alter table clusters add column if not exists pinned_mention_count integer;
