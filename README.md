# Painminer — personal runbook

Machine A runs Painminer. Machine B runs LM Studio. Both machines must be on the same LAN. Turn off the VPN on Machine A before a run.

## One-time setup

### Machine B — LM Studio

1. Open LM Studio and start its local server with LAN access enabled.
2. Load these models. Their IDs must appear in LM Studio's loaded-model list:

   - `qwen-extract`
   - `bge-small`
   - `qwen/qwen3.6-35b-a3b`

3. Leave LM Studio running. Its host and port must match `LLM_BASE_URL` in Machine A's `.env`.

### Machine A — Painminer

From the project folder:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create `.env` once with your Supabase, Telegram, and Machine B details:

```dotenv
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_KEY=<service-role-key>
SUPABASE_DB_URL=postgresql://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres
TELEGRAM_TOKEN=<bot-token>
TELEGRAM_CHAT_ID=<your-chat-id>
LLM_BASE_URL=http://<machine-b-lan-ip>:1234/v1
LLM_MODEL=qwen-extract
EMBED_MODEL=bge-small
```

For a new Supabase database only, enable `vector` in the Supabase SQL editor, then run this once:

```bash
for file in sql/schema.sql sql/add_metadata.sql sql/add_thread_id.sql sql/judge.sql sql/queue.sql sql/queue_fair.sql sql/cluster.sql sql/add_pin.sql; do
  .venv/bin/python -m painminer.tools.migrate "$file"
done
.venv/bin/python -m painminer.tools.seed_sources
```

Do not re-run the database setup for normal daily runs.

## Daily run

1. On Machine B: open LM Studio, start the server, and load the three models above.
2. On Machine A: turn off VPN, open this project folder, then run:

```bash
.venv/bin/python -m painminer.tools.run_pipeline
```

That is it. The digest is saved in `digests/` and sent to your configured Telegram chat.

If it stops at `Preflight: checking Machine B…`, Machine A cannot see LM Studio or one of the three models is not loaded. Check exactly what Machine A sees with:

```bash
.venv/bin/python -c 'from painminer.config import load_config; from painminer.llm import LLM; print("\n".join(LLM(load_config()).list_models()))'
```

Optional: keep the Telegram interface running instead of starting the pipeline manually:

```bash
.venv/bin/python -m painminer.delivery.telegram_bot
```

Then send `/scan` from the configured Telegram chat.
