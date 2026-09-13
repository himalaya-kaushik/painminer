-- painminer — clustering support (§8). Additive migration; run after schema.sql.
--
-- clusters.sources_seen / days_seen are sets and mention_count a count, but a
-- mention must be deduped by (cluster, source, date) first (§8) — two comments
-- in one thread on one day are one mention. The `mentions` table enforces that
-- uniqueness; record_mention() updates the cluster's denormalized sets/count
-- only when a genuinely new mention lands.
--
-- The RPCs also carry the halfvec casts, since PostgREST cannot send a halfvec
-- parameter directly — vectors travel as their text form and are cast here.

create table if not exists mentions (
    id         bigint generated always as identity primary key,
    cluster_id bigint not null references clusters (id) on delete cascade,
    source     text   not null,
    seen_on    date   not null,
    unique (cluster_id, source, seen_on)
);

-- Create a new cluster anchored on its first member's embedding (§8: the
-- centroid is anchored, never recomputed as a running mean).
create or replace function create_cluster(
    p_kind text, p_statement text, p_centroid text
)
returns bigint
language plpgsql
as $$
declare new_id bigint;
begin
    insert into clusters (kind, canonical_statement, centroid, first_seen, last_seen)
    values (p_kind, p_statement, p_centroid::halfvec, now(), now())
    returning id into new_id;
    return new_id;
end;
$$;

-- Persist a finding's embedding (text -> halfvec).
create or replace function set_embedding(p_id bigint, p_vec text)
returns void
language sql
as $$
    update findings set embedding = p_vec::halfvec where id = p_id;
$$;

-- Record one mention, deduped by (cluster, source, seen_on). Returns true if
-- this was a new mention (and the cluster's sets/count were updated).
create or replace function record_mention(
    p_cluster_id bigint, p_source text, p_seen_on date
)
returns boolean
language plpgsql
as $$
begin
    insert into mentions (cluster_id, source, seen_on)
    values (p_cluster_id, p_source, p_seen_on)
    on conflict (cluster_id, source, seen_on) do nothing;

    if not found then
        return false;              -- duplicate mention; nothing to update
    end if;

    update clusters
       set mention_count = mention_count + 1,
           sources_seen  = case when p_source = any(sources_seen)
                                then sources_seen else array_append(sources_seen, p_source) end,
           days_seen     = case when p_seen_on = any(days_seen)
                                then days_seen else array_append(days_seen, p_seen_on) end,
           last_seen     = now()
     where id = p_cluster_id;
    return true;
end;
$$;
