-- painminer — queue fairness (v3 brief §6). Additive migration; run after queue.sql.
--
-- claim_items was id-ordered, so a high-volume source (Hacker News) drained the
-- whole budget before later sources were reached — Lobsters / Stack Overflow /
-- the GitHub tail were starved. This adds an optional p_source filter so the
-- caller can round-robin claims across sources (see pipeline/judge.py). With
-- p_source null the behaviour is exactly as before (claim across all sources).
--
-- The old 3-arg signature is dropped first so there is no overload ambiguity.

drop function if exists claim_items(integer, integer, integer);

create or replace function claim_items(
    batch_size    integer,
    stuck_seconds integer default 3600,
    max_attempts  integer default 3,
    p_source      text default null
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
       and processing_at < now() - make_interval(secs => stuck_seconds)
       and (p_source is null or source = p_source);

    return query
    with candidates as (
        select id
          from items
         where (p_source is null or source = p_source)
           and (state = 'ready'
                or (state = 'processing'
                    and attempts < max_attempts
                    and processing_at < now() - make_interval(secs => stuck_seconds)))
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
