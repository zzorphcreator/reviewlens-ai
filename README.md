# ReviewLens AI

Backend-first review ingestion and RAG prototype.

Current slice:

- FastAPI app serving a static single-page UI.
- Deterministic CSV and JSON review imports.
- URL ingestion through Redis/RQ workers.
- BrightData, Zyte, direct HTTP, Apple App Store RSS, Google Play, and TripAdvisor parsing paths.
- Normalized review storage with per-source dedupe.
- S3 storage for uploaded files so web and worker services can share imports.
- pgvector-backed review embeddings generated after ingestion.
- Saved sessions that group ingested sources and persist chat messages.
- Scoped RAG chat over the current ingested sources or selected saved session.

## Run Locally

Copy `.env.example` to `.env`, then set at minimum:

```bash
DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/reviewlens"
REDIS_URL="redis://localhost:6379/0"
OPENAI_API_KEY="..."
ANTHROPIC_API_KEY="..."
```

Plain `postgresql://` and `postgres://` URLs are accepted; the app converts them to
`postgresql+asyncpg://` internally.

Install dependencies, run migrations, start Redis, then start the app and worker:

```bash
python -m pip install -r requirements.txt
alembic upgrade head
redis-server
python -m uvicorn backend.main:app --reload
rq worker import scrape
```

Open `http://localhost:8000`.

File imports require S3-compatible storage because the web process uploads the file and the worker downloads it later:

```bash
S3_BUCKET="your-bucket"
S3_REGION="us-east-1"
S3_ENDPOINT=""
S3_ACCESS_KEY_ID="..."
S3_SECRET_ACCESS_KEY="..."
```

For AWS S3, leave `S3_ENDPOINT` empty. For Cloudflare R2, MinIO, or another S3-compatible provider, set `S3_ENDPOINT` to that provider endpoint.

## Docker

Build and run the local container stack:

```bash
docker compose up --build
```

The compose stack starts:

- `app`: FastAPI web service on `http://localhost:8082`.
- `worker`: RQ worker listening on `import` and `scrape`.
- `redis`: Redis broker for RQ.

`docker-compose.yml` reads `.env`, overrides `REDIS_URL` to the Compose Redis service, and sets `RUNNING_IN_DOCKER=1`. That guard rejects `DATABASE_URL` values pointing at `localhost` from inside the container. Use a real hostname such as a Docker network service name, host gateway, or external Postgres host.

The Docker image runs migrations before starting the web process:

```bash
alembic upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8082}
```

The worker command is:

```bash
rq worker --url "$REDIS_URL" import scrape
```

## Deployment

The checked-in `render.yaml` deploys two Docker services:

- `reviewlens-ai-web`: FastAPI web/API service, health checked at `/health`.
- `reviewlens-ai-worker`: background worker running `sh ./scripts/start-worker.sh`.

Provision these external resources before deploying:

- PostgreSQL with the `vector` extension available.
- Redis instance for RQ.
- S3 bucket for uploaded review files. You will need to create IAM roles, policies and keys and grant it read/write access to this bucket
- OpenAI API key for embeddings and primary chat.
- Anthropic API key for fallback chat.
- BrightData and/or Zyte credentials for managed URL scraping.
- Langsmith for logging LLM interaction

Required Render environment variables:

```bash
DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/reviewlens"
REDIS_URL="redis://..."
RUNNING_IN_DOCKER="1"
UPLOAD_DIR="/app/uploads"

S3_BUCKET="..."
S3_REGION="..."
S3_ENDPOINT=""
S3_ACCESS_KEY_ID="..."
S3_SECRET_ACCESS_KEY="..."

OPENAI_API_KEY="..."
ANTHROPIC_API_KEY="..."
OPENAI_CHAT_MODEL="gpt-5.4"
ANTHROPIC_FALLBACK_MODELS="claude-haiku-4.7,claude-sonnet-4.7"
EMBEDDING_MODEL="text-embedding-3-small"
EMBEDDING_DIMENSIONS="1536"
RAG_TOP_K="8"

SCRAPER_PROVIDER_ORDER="brightdata,zyte,http"
BRIGHTDATA_API_KEY="..."
BRIGHTDATA_ZONE="..."
ZYTE_API_KEY="..."

LANGSMITH_TRACING="false"
LANGSMITH_API_KEY=""
LANGSMITH_PROJECT="reviewlens-ai"
LANGSMITH_ENDPOINT="https://api.smith.langchain.com"
LANGSMITH_WORKSPACE_ID=""
```

Notes:

- Use the external PostgreSQL hostname on Render, not an internal/local URL from your laptop.
- The web service runs `alembic upgrade head` on startup, so migrations apply before Uvicorn starts.
- The worker does not run migrations; it assumes the web service or deployment job has already upgraded the schema.
- `SCRAPER_PROVIDER_ORDER="brightdata,zyte,http"` is the recommended production order because public sites often block direct HTTP.
- Keep `LANGSMITH_TRACING=false` until you explicitly want traces. When enabled, API keys are redacted and embedding vectors are summarized.

## LLM and RAG

Ingestion writes review embeddings after reviews are inserted. Chat is strictly scoped to either the selected saved session or the current UI workspace source IDs.

OpenAI is used for embeddings and primary chat. If the OpenAI chat request fails before producing an answer, the app tries Anthropic models in `ANTHROPIC_FALLBACK_MODELS` order.

Current defaults:

```bash
OPENAI_CHAT_MODEL="gpt-5.4"
ANTHROPIC_FALLBACK_MODELS="claude-haiku-4.7,claude-sonnet-4.7"
EMBEDDING_MODEL="text-embedding-3-small"
EMBEDDING_DIMENSIONS="1536"
RAG_TOP_K="8"
```

Changing `EMBEDDING_DIMENSIONS` requires a matching Alembic migration and a full re-embed.

## Scraper Providers

URL ingestion tries scraper providers in `SCRAPER_PROVIDER_ORDER` until one returns parseable reviews. Supported generic providers are `http`, `brightdata`, and `zyte`.

```bash
SCRAPER_PROVIDER_ORDER="brightdata,zyte,http"
SCRAPER_TIMEOUT_SECONDS="30"
BRIGHTDATA_API_KEY="..."
BRIGHTDATA_ZONE="..."
BRIGHTDATA_API_URL="https://api.brightdata.com/request"
BRIGHTDATA_PROXY_URL=""
BRIGHTDATA_VERIFY_SSL="true"
BRIGHTDATA_TIMEOUT_SECONDS="60"
BRIGHTDATA_DEBUG_DUMP_HTML="false"
ZYTE_API_KEY="..."
ZYTE_API_URL="https://api.zyte.com/v1/extract"
ZYTE_BROWSER_HTML="true"
ZYTE_TIMEOUT_SECONDS="60"
SCRAPER_DEBUG_DUMP_HTML="false"
```

Platform-specific paths run before the generic provider loop:

- Apple App Store URLs use Apple public RSS JSON.
- Google Play URLs use `google-play-scraper`.
- TripAdvisor pages use a custom HTML parser after fetching.

Provider attempts are saved in ingest job stats so failures are visible in the UI/status response.

## Database Migrations

Create or update tables with Alembic:

```bash
alembic upgrade head
```

Create a new migration after model changes:

```bash
alembic revision --autogenerate -m "describe change"
```

The first pgvector migration enables the extension and creates the vector table/index. The configured database user must be allowed to run:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

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

Unknown fields are rejected intentionally so the ingestion contract stays deterministic.

## Test

```bash
TEST_DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/reviewlens_test" python -m pytest
```

DB/API tests run `alembic upgrade head` automatically against `TEST_DATABASE_URL`.
