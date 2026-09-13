-- painminer — queue primitives (§4.2). Additive migration; run after schema.sql.
--
-- The queue is claim-don't-read: a worker atomically flips rows to
-- 'processing' with a timestamp before touching them, so a crash leaves a
-- clear trail and nothing is silently lost. Single-operator today, but the
-- claim is atomic (FOR UPDATE SKIP LOCKED) so it stays correct if a second
-- worker ever runs.

-- Claim up to batch_size items for processing.
--
--   * picks rows in state 'ready', plus 'processing' rows stuck longer than
--     stuck_seconds (reclaim after a crash),
--   * but first retires rows that have already used up their attempts
--     (three strikes -> 'failed', §4.2), so a poison row can never loop,
--   * increments attempts and stamps processing_at on every claim,
--   * returns the claimed rows.
create or replace function claim_items(
    batch_size    integer,
    stuck_seconds integer default 3600,
    max_attempts  integer default 3
)
returns setof items
language plpgsql
as $$
begin
    -- Retire poison rows: stuck in processing and out of attempts.
    update items
       set state = 'failed'
     where state = 'processing'
       and attempts >= max_attempts
       and processing_at < now() - make_interval(secs => stuck_seconds);

    return query
    with candidates as (
        select id
          from items
         where state = 'ready'
            or (state = 'processing'
                and attempts < max_attempts
                and processing_at < now() - make_interval(secs => stuck_seconds))
         order by id
         limit batch_size
         for update skip locked
    )
    update items i
       set state = 'processing',
           processing_at = now(),
           attempts = i.attempts + 1
      from candidates c
     where i.id = c.id
    returning i.*;
end;
$$;
