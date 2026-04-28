# ReviewLens AI

Backend-first review ingestion prototype.

Current slice:

- FastAPI app serving a static single-page UI.
- Deterministic CSV, JSON, and JSONL review imports.
- Async-style import jobs backed by Redis/RQ.
- Normalized review storage with per-source dedupe.
- Saved sessions that group ingested sources and store chat messages.
- pgvector-backed review embeddings generated after ingestion.
- Scoped RAG chat over the current ingested sources or selected saved session.

## Run Locally

```bash
python -m pip install -e ".[dev]"
alembic upgrade head
python -m uvicorn backend.main:app --reload
```

Open `http://localhost:8000`.

Copy `.env.example` to `.env` and point `DATABASE_URL` at PostgreSQL:

```bash
DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/reviewlens"
UPLOAD_DIR="uploads"
```

Plain `postgresql://` URLs are accepted; the app converts them to SQLAlchemy's async
`postgresql+asyncpg://` driver URL internally.

## LLM and RAG

Ingestion writes review embeddings after reviews are inserted. Chat is strictly scoped to either
the selected saved session or the current UI workspace source IDs.

```bash
OPENAI_API_KEY="..."
ANTHROPIC_API_KEY="..."
OPENAI_CHAT_MODEL="gpt-5.4"
ANTHROPIC_FALLBACK_MODELS="claude-haiku-4.7,claude-sonnet-4.7"
EMBEDDING_MODEL="text-embedding-3-small"
EMBEDDING_DIMENSIONS="1536"
RAG_TOP_K="8"
LANGSMITH_TRACING="false"
LANGSMITH_API_KEY="..."
LANGSMITH_PROJECT="reviewlens-ai"
```

OpenAI is used for embeddings and as the primary chat provider. If the OpenAI chat request fails,
the app tries Anthropic models in `ANTHROPIC_FALLBACK_MODELS` order.
When `LANGSMITH_TRACING=true`, embeddings, retrieval, and chat provider calls are traced with
LangSmith. API keys are redacted from traced settings, and embedding vectors are summarized.

## Database Migrations

Create or update tables with Alembic:

```bash
alembic upgrade head
```

Create a new migration after model changes:

```bash
alembic revision --autogenerate -m "describe change"
```

Redis/RQ workers are required for ingestion jobs:
```bash
rq worker import scrape
```

File uploads are stored in S3 so workers can access them:
```bash
S3_BUCKET="your-bucket"
S3_REGION="us-east-1"
S3_ACCESS_KEY_ID="..."
S3_SECRET_ACCESS_KEY="..."
S3_ENDPOINT=""
```

## Scraper Providers

URL ingestion tries scraper providers in `SCRAPER_PROVIDER_ORDER` until one returns parseable
reviews. Supported values are `http`, `brightdata`, and `zyte`.

```bash
SCRAPER_PROVIDER_ORDER="brightdata,zyte,http"
BRIGHTDATA_API_KEY="..."
BRIGHTDATA_ZONE="..."
BRIGHTDATA_API_URL="https://api.brightdata.com/request"
BRIGHTDATA_TIMEOUT_SECONDS="60"
BRIGHTDATA_DEBUG_DUMP_HTML="false"
ZYTE_API_KEY="..."
ZYTE_API_URL="https://api.zyte.com/v1/extract"
ZYTE_BROWSER_HTML="true"
ZYTE_TIMEOUT_SECONDS="60"
SCRAPER_DEBUG_DUMP_HTML="false"
```

Bright Data is used through its Web Unlocker API when `BRIGHTDATA_API_KEY` and `BRIGHTDATA_ZONE`
are set; `BRIGHTDATA_PROXY_URL` is still supported as a fallback. Zyte uses the Extraction API
with `browserHtml=true`. Provider attempts are saved in the ingest job stats so failures are visible.

## Import Schema

Required fields:

- `author`
- `rating` from 0 to 5
- `body`
- `reviewed_at`

Optional fields:

- `title`
- `source_url`
- `metadata`

Unknown fields are rejected intentionally so the first ingestion contract stays deterministic.

## Test

```bash
TEST_DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/reviewlens_test" python -m pytest
```

DB/API tests run `alembic upgrade head` automatically against `TEST_DATABASE_URL`.
