-- painminer — reset all pipeline data before the real end-to-end run.
-- Clears the accumulated test/dev rows so the database holds only real output
-- (operator asked not to leave it full of test data). Keeps the real source
-- registry; drops throwaway test sources; sets a bounded recent watermark so
-- the run fetches a manageable window.

truncate table mentions, feedback, findings, clusters, items, runs
    restart identity cascade;

delete from sources
    where name not in ('hackernews', 'github', 'lobsters', 'stackoverflow');

update sources
    set last_successful_fetch_at = now() - interval '6 hours'
    where enabled;
