-- painminer — judge commit (§6, §7). Additive migration; run after schema.sql.
--
-- Records a judged item atomically: insert its 0..n findings, null its
-- raw_text, and mark it done — all in one transaction (§6). raw_text is
-- cleared here, in the same transaction as success, not by a later cleanup
-- job, so raw text never lingers and a done item is always text-free.

create or replace function record_judgement(
    p_item_id  bigint,
    p_findings jsonb
)
returns integer
language plpgsql
as $$
declare
    n integer;
begin
    insert into findings
        (item_id, kind, statement, why_it_matters, domain, evidence_quote, confidence)
    select
        p_item_id,
        f->>'kind',
        f->>'statement',
        f->>'why_it_matters',
        f->>'domain',
        f->>'evidence_quote',
        (f->>'confidence')::double precision
    from jsonb_array_elements(p_findings) as f;

    get diagnostics n = row_count;

    update items
       set raw_text = null,
           state = 'done'
     where id = p_item_id;

    return n;   -- number of findings written (0 is valid and expected)
end;
$$;
