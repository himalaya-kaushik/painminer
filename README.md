# Painminer

Painminer scans sources (Hacker News, and other adapters in `painminer/adapters/`) for posts that
describe a real pain point, uses an LLM to triage, deep-read, and extract findings from each item,
clusters recurring findings, and synthesizes a nightly digest that's sent to Telegram.

It needs two backing services: a Postgres/Supabase database, and an OpenAI-compatible LLM endpoint
(e.g. a local [LM Studio](https://lmstudio.ai) server) for triage, extraction, and embeddings.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create `.env` with the following:

```dotenv
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_KEY=<service-role-key>
TELEGRAM_TOKEN=<bot-token>
TELEGRAM_CHAT_ID=<your-chat-id>
LLM_BASE_URL=http://<llm-host>:1234/v1
LLM_MODEL=<extraction-model-id>
```

`LLM_BASE_URL` must point to an OpenAI-compatible endpoint (LM Studio, vLLM, etc.) that serves the
model named by `LLM_MODEL`. Embeddings use the same endpoint; the model id defaults to `bge-small`
and can be overridden with `EMBED_MODEL`. See `painminer/config.py` for the full list of optional
overrides (timeouts, run caps, clustering thresholds, digest limits, etc.) and their defaults.

### Database migration (one-time, per database)

Enable the `vector` extension in the Supabase SQL editor, then run:

```bash
for file in sql/schema.sql sql/add_metadata.sql sql/add_thread_id.sql sql/judge.sql sql/queue.sql sql/queue_fair.sql sql/cluster.sql sql/add_pin.sql; do
  .venv/bin/python -m painminer.tools.migrate "$file"
done
.venv/bin/python -m painminer.tools.seed_sources
```

Do not re-run this for normal runs.

## Running

Make sure the LLM server is up and has the extraction and embedding models loaded, then:

```bash
.venv/bin/python -m painminer.tools.run_pipeline
```

This scans configured sources, triages and extracts findings, clusters them, synthesizes a digest,
writes it to `digests/`, and sends it to the configured Telegram chat.

### Troubleshooting

If it stops at `Preflight: checking …`, painminer can't reach the LLM endpoint, or one of the
required models isn't loaded there. Check what's actually visible at `LLM_BASE_URL`:

```bash
.venv/bin/python -c 'from painminer.config import load_config; from painminer.llm import LLM; print("\n".join(LLM(load_config()).list_models()))'
```

### Optional: Telegram bot mode

Instead of running the pipeline manually, you can keep a long-running bot process and trigger a
scan on demand:

```bash
.venv/bin/python -m painminer.delivery.telegram_bot
```

Then send `/scan` from the configured Telegram chat.
