-- Nightly GitHub Actions trigger.
--
-- GitHub's own `schedule:` cron delayed this run by 2-4 hours on consecutive
-- nights (its schedule queue is global and best-effort). pg_cron runs inside
-- this database and fires on the minute, so the trigger lives here instead.
-- The workflow is `workflow_dispatch:`-only; do not add `schedule:` back.
--
-- Times are UTC (this database runs UTC). 16:15 UTC = 21:45 IST year-round;
-- IST has no DST, so this never needs seasonal adjustment.

create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;

create table if not exists public.nightly_dispatch_log (
  id          bigserial primary key,
  request_id  bigint      not null,
  attempt     int         not null default 1,
  created_at  timestamptz not null default now(),
  checked     boolean     not null default false,
  status_code int,
  note        text
);

alter table public.nightly_dispatch_log enable row level security;

comment on table public.nightly_dispatch_log is
  'One row per workflow_dispatch POST to GitHub. check_nightly_dispatch() reconciles each row against net._http_response.';

-- Telegram helper. Used only for dispatch-layer failures; the pipeline itself
-- reports through its own Telegram path inside the workflow.
create or replace function public.notify_telegram(msg text)
returns bigint
language plpgsql
security definer
set search_path = public, vault, net
as $$
declare
  tok  text;
  chat text;
begin
  select decrypted_secret into tok  from vault.decrypted_secrets where name = 'telegram_token';
  select decrypted_secret into chat from vault.decrypted_secrets where name = 'telegram_chat_id';

  return net.http_post(
    url     := 'https://api.telegram.org/bot' || tok || '/sendMessage',
    body    := jsonb_build_object('chat_id', chat, 'text', msg),
    headers := jsonb_build_object('Content-Type', 'application/json'),
    timeout_milliseconds := 15000
  );
end;
$$;

-- Fire the workflow. pg_net is async: this returns a request id immediately and
-- the response lands in net._http_response later, which is why the companion
-- checker below exists.
create or replace function public.dispatch_nightly(p_attempt int default 1)
returns bigint
language plpgsql
security definer
set search_path = public, vault, net
as $$
declare
  pat text;
  req bigint;
begin
  select decrypted_secret into pat from vault.decrypted_secrets where name = 'github_pat';

  if pat is null or pat = 'PLACEHOLDER' then
    perform public.notify_telegram('painminer: nightly NOT triggered - github_pat is not set in Supabase Vault.');
    raise exception 'github_pat not configured in vault';
  end if;

  req := net.http_post(
    url  := 'https://api.github.com/repos/himalaya-kaushik/painminer/actions/workflows/nightly.yml/dispatches',
    body := jsonb_build_object('ref', 'main'),
    headers := jsonb_build_object(
      'Authorization',        'Bearer ' || pat,
      'Accept',               'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      -- GitHub rejects API requests with no User-Agent.
      'User-Agent',           'painminer-pg-cron',
      'Content-Type',         'application/json'
    ),
    timeout_milliseconds := 15000
  );

  insert into public.nightly_dispatch_log (request_id, attempt) values (req, p_attempt);
  return req;
end;
$$;

-- pg_net has no retry of its own. This reconciles each logged request against
-- its response: retry up to 3 attempts total, then alert.
create or replace function public.check_nightly_dispatch()
returns void
language plpgsql
security definer
set search_path = public, vault, net
as $$
declare
  r      record;
  resp   record;
  status int;
begin
  for r in
    select * from public.nightly_dispatch_log
    where not checked and created_at < now() - interval '45 seconds'
    order by id
  loop
    select * into resp from net._http_response where id = r.request_id;

    if not found then
      -- pg_net prunes responses after ~6h; treat a vanished row as a miss.
      if r.created_at < now() - interval '10 minutes' then
        update public.nightly_dispatch_log
           set checked = true, note = 'no response row found'
         where id = r.id;
      end if;
      continue;
    end if;

    status := resp.status_code;

    update public.nightly_dispatch_log
       set checked = true, status_code = status,
           note = coalesce(resp.error_msg, left(resp.content, 400))
     where id = r.id;

    -- GitHub documents 204 for this endpoint but has returned 200; accept any 2xx.
    if status between 200 and 299 then
      continue;                                  -- dispatch accepted
    elsif r.attempt < 3 then
      perform public.dispatch_nightly(r.attempt + 1);
    else
      perform public.notify_telegram(
        'painminer: nightly trigger FAILED after 3 attempts. HTTP ' ||
        coalesce(status::text, 'none') ||
        ' - ' || coalesce(resp.error_msg, left(resp.content, 200), 'no detail') ||
        E'\nCheck the PAT in Supabase Vault (it may have expired).'
      );
    end if;
  end loop;
end;
$$;

-- 16:15 UTC = 21:45 IST
select cron.schedule('nightly-dispatch', '15 16 * * *', $job$select public.dispatch_nightly();$job$);

-- Reconcile/retry for the following hour. Cheap no-op outside the retry window.
select cron.schedule('nightly-dispatch-check', '*/2 16-17 * * *', $job$select public.check_nightly_dispatch();$job$);
