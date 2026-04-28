# ReviewLens AI — System Design Document

**Version:** 1.0  
**Date:** 2026-04-27  
**Status:** Draft

---

## 1. Executive Summary

ReviewLens AI is a web-based platform that aggregates product/service reviews from public review sites and local files, stores them in PostgreSQL with the pgvector extension, and exposes a RAG-powered chat interface for querying the ingested reviews. The system uses LangChain to orchestrate multi-provider LLM calls (OpenAI primary, Anthropic fallback), background task queues for non-blocking ingestion, and a layered scraping strategy to handle sites that restrict automated access. All persistent state — relational data, embeddings, and uploaded files — lives in a single PostgreSQL instance, keeping the infrastructure footprint minimal.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          CLIENT BROWSER                             │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │  Plain HTML + Vanilla JS (Tailwind CDN)                      │  │
│   │  ┌─────────────────┐  ┌─────────────┐  ┌─────────────────┐  │  │
│   │  │  Review Ingestion│  │  Chat UI    │  │  Dashboard /    │  │  │
│   │  │  (URL + File)    │  │  (WebSocket)│  │  Review Browser │  │  │
│   │  └────────┬────────┘  └──────┬──────┘  └────────┬────────┘  │  │
│   └───────────┼──────────────────┼───────────────────┼───────────┘  │
└───────────────┼──────────────────┼───────────────────┼─────────────┘
                │ REST             │ WebSocket          │ REST
                ▼                  ▼                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│               API GATEWAY (Render — managed, no config)             │
│              Rate Limiting · TLS Termination · CORS                 │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     FastAPI Application Server                      │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────────┐ │
│  │  /api/ingest │  │  /api/chat   │  │  /api/reviews             │ │
│  │  (URL/File)  │  │  (WS stream) │  │  (CRUD + search)          │ │
│  └──────┬───────┘  └──────┬───────┘  └───────────────────────────┘ │
└─────────┼─────────────────┼───────────────────────────────────────┘
          │                 │
          ▼                 ▼
┌──────────────────┐  ┌─────────────────────────────────────────────┐
│  TASK QUEUE      │  │  LLM / RAG LAYER                            │
│  Redis + RQ      │  │                                             │
│                  │  │  LangChain LCEL Pipeline                    │
│  ┌────────────┐  │  │  ┌────────────────────────────────────────┐ │
│  │ Scrape Job │  │  │  │  Retriever → Prompt → LLM → Response  │ │
│  │ File Job   │  │  │  └─────────────────────────────────────── ┘ │
│  │ Embed Job  │  │  │                                             │
│  └─────┬──────┘  │  │  Primary: OpenAI gpt-4o                     │
└────────┼─────────┘  │  Fallback: Anthropic claude-sonnet-4-6       │
         │            │  Tracing:  Langfuse                          │
         ▼            └─────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│                     INGESTION LAYER                                 │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Scraper Router                                              │   │
│  │                                                              │   │
│  │  Tier 1: Direct HTTP + BeautifulSoup  (free, fast)          │   │
│  │  Tier 2: Playwright / Scrapy          (JS-heavy sites)      │   │
│  │  Tier 3: BrightData / Zyte / Firecrawl (blocked sites)      │   │
│  │                                                              │   │
│  │  Site Adapters:                                              │   │
│  │  ┌──────────┐ ┌────────────┐ ┌──────┐ ┌──────────────────┐ │   │
│  │  │ Amazon   │ │ Google Maps│ │  G2  │ │ Capterra/Yelp/.. │ │   │
│  │  └──────────┘ └────────────┘ └──────┘ └──────────────────┘ │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  File Processor (CSV / JSON)                                 │   │
│  │  Schema validation → normalise → chunk → embed              │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         STORAGE LAYER                               │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  PostgreSQL 16 + pgvector extension                          │   │
│  │                                                              │   │
│  │  review_sources   — source metadata & config                │   │
│  │  ingest_jobs      — job status tracking                     │   │
│  │  reviews          — raw review records                      │   │
│  │  review_chunks    — text chunks + vector(1536) embeddings   │   │
│  │  chat_sessions    — session metadata                        │   │
│  │  chat_messages    — conversation history                    │   │
│  │                                                              │   │
│  │  HNSW index on review_chunks(embedding vector_cosine_ops)   │   │
│  │  GIN  index on review_chunks for full-text hybrid search    │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Detail

### 3.1 Frontend

Plain HTML + vanilla JS, served as static files directly by FastAPI via `StaticFiles`. No build step, no npm, no separate Render service.

| Concern | Choice | Rationale |
|---|---|---|
| Markup | Plain HTML | No build pipeline |
| Styling | Tailwind CSS (CDN) | Utility classes without a build step |
| Scripting | Vanilla JS (ES modules) | No framework overhead |
| Chat streaming | Native `WebSocket` API | Built into every browser |
| File upload | Native `<input type="file">` | No library needed |
| Chat history | `sessionStorage` | Per-session, no server state |
| Served by | FastAPI `StaticFiles` mount | Single service, no separate frontend deploy |

```python
# main.py
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
```

**Pages (`frontend/`):**

```
frontend/
├── index.html      # dashboard: source list, ingest status
├── ingest.html     # add source (URL input or file upload)
├── chat.html       # chat interface with model selector
├── reviews.html    # browse / filter ingested reviews
├── css/
│   └── app.css     # minimal custom styles on top of Tailwind CDN
└── js/
    ├── api.js      # fetch/WebSocket wrappers
    ├── chat.js     # streaming chat logic
    ├── ingest.js   # ingest form + job polling
    └── reviews.js  # review browser
```

---

### 3.2 FastAPI Application Server

```
backend/
├── main.py
├── api/
│   ├── routes/
│   │   ├── ingest.py       # POST /ingest/url, POST /ingest/file
│   │   ├── chat.py         # WS  /chat/{session_id}
│   │   ├── reviews.py      # GET /reviews, GET /reviews/{id}
│   │   └── sources.py      # CRUD for review sources
│   └── middleware/
│       ├── rate_limit.py   # slowapi (token bucket per IP)
│       └── security.py     # CSP, CORS, input sanitisation
├── ingestion/
│   ├── router.py           # picks scraping tier
├── scrapers
│   ├── models.py
│   ├── parsers
│   │   ├── g2.py
│   │   ├── review_html.py
│   │   └── tripadvisor.py
|	|	└── <more parsers>.py
│   ├── providers
│   │   ├── apple.py
│   │   ├── google_maps.py
│   │   ├── brightdata.py
│   │   ├── local_scrappy.py
│   │   ├── zyte.py
│   ├── tools
│   │   └── review_site_scraper_tool.py
│   └── utils
│       ├── pagination.py
│       └── <other>.py
│   └── file_processor.py   # CSV/JSON normaliser
├── llm/
│   ├── chain.py            # LangChain LCEL RAG chain
│   ├── models.py           # OpenAI + Anthropic with fallback
│   └── embeddings.py       # OpenAI text-embedding-3-small
├── storage/
│   ├── vector_store.py     # PGVector (langchain_postgres) wrapper
│   └── database.py         # SQLAlchemy async + Alembic
├── workers/
│   ├── queues.py           # Queue definitions (scrape, embed, file)
│   └── tasks.py            # RQ job functions: scrape_url_task, process_file_task, embed_task
└── config.py               # pydantic-settings
```

**Ingest flow (async, non-blocking):**

```
POST /ingest/url  ──► validate URL
                      ──► create ingest_job record (status=PENDING)
                      ──► enqueue RQ job (scrape_url_task)
                      ──► return { job_id }
                                    │
                                    ▼ (background)
                          scrape_url_task
                          ──► ScraperRouter.scrape()
                          ──► normalise → ReviewDocument[]
                          ──► chunk text (RecursiveCharacterTextSplitter)
                          ──► embed_task (OpenAI embeddings)
                          ──► INSERT INTO review_chunks (pgvector)
                          ──► update ingest_job (status=DONE)

GET /ingest/jobs/{job_id}  ──► poll status (SSE or polling)
```

---

### 3.3 Scraping Layer — Tiered Strategy

Review sites range from fully open to heavily bot-protected. A tiered approach minimises cost:

```
ScraperRouter.scrape(url, platform)
│
├── Tier 1 — Direct HTTP (httpx async + BeautifulSoup)
│   Cost: $0    Speed: fast    Coverage: ~30% of targets
│   Used for: G2, Capterra (partial), Yelp (with user-agent rotation)
│
├── Tier 2 — Headless Browser (Playwright, async)
│   Cost: CPU   Speed: slow    Coverage: +40%
│   Used for: Google Maps, JS-rendered review widgets
│   Pool: 4 browser workers via playwright-pool
│
└── Tier 3 — Managed Proxy APIs
    Cost: $$    Speed: medium  Coverage: +30% (Amazon, heavily protected)
    Providers (tried in order until success):
    ├── BrightData — SERP API / Web Unlocker (primary)
    ├── Zyte       — Automatic Extraction API (secondary fallback)
    └── Firecrawl  — to be evaluated based on BrightData results

    Fallback order can be configured per-platform in config.
```

**Site-specific notes:**

| Platform | Preferred Tier | Notes |
|---|---|---|
| Amazon | — | Deferred to later phase |
| Google Maps | Places API | Official API; requires Google Cloud billing |
| G2 | Tier 1 → Tier 3 | Rate-limited but often accessible |
| Capterra | Tier 1 → Tier 2 | Requires JavaScript for full load |
| Yelp | Tier 1 (Fusion API) | Free API tier available, prefer it |
| TripAdvisor | Tier 2 → Tier 3 | No public API |

**Note on Scrapy + asyncio:** Scrapy is Twisted-based and does not integrate cleanly with FastAPI's asyncio event loop or RQ workers. For paginated crawls the recommended approach is a Playwright-based pagination loop inside an RQ job (each page fetched with `async_playwright` via `asyncio.run()`). Scrapy can be used as a separate crawl process invoked via `subprocess.run` if you have strong reasons to prefer it, but it should not be imported directly into the FastAPI/RQ process.

---

### 3.4 Queue System — Redis RQ

RQ (Redis Queue) uses the same Redis instance that backs the cache, with no separate broker config or celeryconfig. Workers are plain Python processes.

```python
# workers/queues.py
from redis import Redis
from rq import Queue

redis_conn = Redis.from_url(settings.REDIS_URL)

scrape_queue = Queue("scrape", connection=redis_conn)  # 4 workers, I/O bound
embed_queue  = Queue("embed",  connection=redis_conn)  # 2 workers, rate-limited
file_queue   = Queue("file",   connection=redis_conn)  # 2 workers, CPU bound
```

```python
# Enqueueing a job from FastAPI
from rq import Retry

job = scrape_queue.enqueue(
    scrape_url_task,
    url,
    job_id=str(ingest_job.id),
    retry=Retry(max=3, interval=[60, 120, 240]),  # exponential backoff
    job_timeout=600,
)
```

```python
# workers/tasks.py — RQ jobs are plain functions
def scrape_url_task(url: str, source_id: str) -> None:
    import asyncio
    asyncio.run(_scrape_async(url, source_id))

async def _scrape_async(url: str, source_id: str) -> None:
    docs = await ScraperRouter().scrape(url)
    # normalise → chunk → embed → INSERT review_chunks
    ...
```

**Starting workers:**
```bash
rq worker scrape embed file --with-scheduler
```

**Monitoring:** `rq-dashboard` (drop-in web UI, single pip install).

**Why RQ over Celery for this project:**
- Zero config — no celeryconfig, no result backend setup, no `@app.task` decorators
- Jobs are plain functions; easy to test in isolation
- `rq-dashboard` is simpler than Flower for a small team
- Redis already in the stack for cache; no additional broker
- Upgrade path to Celery is straightforward if routing complexity grows

Redis also serves as the cache layer (rate-limit counters, embedding deduplication).

---

### 3.5 LLM / RAG Layer

```python
# Simplified LangChain LCEL pipeline (llm/chain.py)

from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

chain = (
    {"context": retriever | format_docs, "question": RunnablePassthrough()}
    | review_rag_prompt
    | llm_with_fallback          # see below
    | StrOutputParser()
)
```

**Model configuration with fallback:**

```python
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableWithFallbacks

primary = ChatOpenAI(
    model="gpt-4o",
    streaming=True,
    temperature=0.2,
)

fallback = ChatAnthropic(
    model="claude-sonnet-4-6",
    streaming=True,
    temperature=0.2,
)

llm_with_fallback: RunnableWithFallbacks = primary.with_fallbacks(
    [fallback],
    exceptions_to_handle=(RateLimitError, APIConnectionError),
)
```

**RAG retrieval — pgvector:**

```python
from langchain_postgres import PGVector
from langchain_openai import OpenAIEmbeddings

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")  # 1536 dims

vector_store = PGVector(
    connection=settings.ASYNC_DATABASE_URL,
    collection_name="review_chunks",
    embeddings=embeddings,
    use_jsonb=True,
)

# Scoped to one or more sources; filter is pushed into the SQL WHERE clause,
# not evaluated post-retrieval, so it is not bypassable via prompt.
retriever = vector_store.as_retriever(
    search_type="mmr",
    search_kwargs={
        "k": 8,
        "fetch_k": 30,           # MMR candidate pool
        "filter": {"source_id": {"$in": selected_source_ids}},
    },
)
```

The `langchain_postgres.PGVector` integration issues queries of the form:
```sql
SELECT content, metadata, 1 - (embedding <=> $1) AS score
FROM langchain_pg_embedding
WHERE collection_id = $2
  AND (metadata->>'source_id') = ANY($3)
ORDER BY embedding <=> $1
LIMIT 30;
```
— MMR re-ranking happens in Python after this fetch.

**Embedding dimensions and HNSW index** (in the Alembic migration):
```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE review_chunks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id   uuid NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    source_id   uuid NOT NULL REFERENCES review_sources(id) ON DELETE CASCADE,
    chunk_index int  NOT NULL,
    content     text NOT NULL,
    embedding   vector(1536),        -- matches text-embedding-3-small
    metadata    jsonb DEFAULT '{}'
);

-- HNSW: fast approximate search, better query latency than IVFFlat
CREATE INDEX review_chunks_embedding_idx
    ON review_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Full-text index for optional hybrid (BM25 + vector) search
CREATE INDEX review_chunks_fts_idx
    ON review_chunks USING gin(to_tsvector('english', content));
```

**Embedding provider fallback:** `text-embedding-3-small` is the primary. Anthropic does not ship embeddings — if OpenAI is unavailable, fall back to **Voyage AI** (`voyage-3-lite`, also 1024 dims, requires re-indexing) or a local `BAAI/bge-small-en-v1.5` (768 dims via sentence-transformers). Switching providers requires a full re-embed since dimensions differ; this is a rare operational event. The embedding model is stored per `review_source` so mixed-provider indexes are detectable.

**Empty-context guard:**
```python
def build_chain(retriever):
    def guard_empty_context(inputs):
        if not inputs["context"].strip():
            return "No reviews have been loaded for this source yet."
        return None

    return (
        RunnablePassthrough.assign(context=retriever | format_docs)
        | RunnableLambda(lambda x: guard_empty_context(x) or x)
        | review_rag_prompt
        | llm_with_fallback
        | StrOutputParser()
    )
```

**Prompt design (system):**
```
You are a review analysis assistant. Answer only using the review excerpts 
provided below. If the answer cannot be found in the reviews, say so. 
Do not make up information or draw on external knowledge.

Reviews:
{context}
```

---

### 3.6 Storage Layer

All persistent state lives in a single PostgreSQL 16 instance with the `pgvector` extension. There is no separate vector database service.

**Full schema (via SQLAlchemy async + asyncpg + Alembic):**

```sql
-- Metadata tables
review_sources (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    url         text,
    platform    text,               -- amazon | google_maps | g2 | capterra | file | ...
    embedding_model text NOT NULL DEFAULT 'text-embedding-3-small',
    created_at  timestamptz DEFAULT now(),
    config      jsonb DEFAULT '{}'  -- scraper-specific settings
)

ingest_jobs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   uuid NOT NULL REFERENCES review_sources(id) ON DELETE CASCADE,
    status      text NOT NULL,      -- pending | running | done | failed
    error       text,
    started_at  timestamptz,
    finished_at timestamptz
)

reviews (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   uuid NOT NULL REFERENCES review_sources(id) ON DELETE CASCADE,
    author      text,
    rating      numeric(3,1),
    body        text NOT NULL,
    reviewed_at timestamptz,
    raw         jsonb DEFAULT '{}'  -- original scraped payload
)

-- Vector store: replaces Weaviate/Chroma entirely
review_chunks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id   uuid NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    source_id   uuid NOT NULL REFERENCES review_sources(id) ON DELETE CASCADE,
    chunk_index int NOT NULL,
    content     text NOT NULL,
    embedding   vector(1536),       -- text-embedding-3-small; update if model changes
    metadata    jsonb DEFAULT '{}'
)

chat_sessions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_ids  uuid[] NOT NULL,
    created_at  timestamptz DEFAULT now()
)

chat_messages (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role        text NOT NULL,      -- user | assistant
    content     text NOT NULL,
    model_used  text,
    latency_ms  int,
    created_at  timestamptz DEFAULT now()
)
```

**Indexes:**

```sql
-- Approximate nearest-neighbour search (HNSW is faster at query time than IVFFlat)
CREATE INDEX review_chunks_embedding_idx
    ON review_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Source-scoped filtering (hits before ANN in the planner)
CREATE INDEX review_chunks_source_idx ON review_chunks (source_id);

-- Full-text for optional hybrid search
CREATE INDEX review_chunks_fts_idx
    ON review_chunks USING gin(to_tsvector('english', content));

-- Fast job status polling
CREATE INDEX ingest_jobs_status_idx ON ingest_jobs (status, started_at DESC);
```

**Why pgvector over a dedicated vector database:**
- One service to run, back up, and operate — dramatically simpler Docker Compose and ops
- Familiar SQL tooling for debugging (inspect embeddings, delete by source, check counts)
- HNSW in pgvector at this scale (tens of millions of review chunks) comfortably matches query latency of self-hosted Weaviate
- Source-scoped filtering via a standard `WHERE source_id = ANY($1)` clause — no tenancy configuration required
- If scale demands it, the migration path to pgvector on managed Postgres (RDS, AlloyDB, Supabase) or to a dedicated vector DB is straightforward: embeddings are just vectors


---

### 3.7 Observability — Langfuse (recommended over LangSmith)

**Why Langfuse over LangSmith:**

| Feature | LangSmith | Langfuse |
|---|---|---|
| Open source | No | Yes (MIT) |
| Self-hostable | No | Yes (Docker Compose) |
| LangChain integration | Native | Native (CallbackHandler) |
| Datasets + Evals | Yes | Yes |
| Cost | Paid above free tier | Free self-hosted |
| Prompt management | Yes | Yes |

**Integration is one line:**

```python
from langfuse.callback import CallbackHandler

langfuse_handler = CallbackHandler(
    public_key=settings.LANGFUSE_PUBLIC_KEY,
    secret_key=settings.LANGFUSE_SECRET_KEY,
    host=settings.LANGFUSE_HOST,  # self-hosted URL
)

# Pass to any LangChain invocation
chain.invoke({"question": q}, config={"callbacks": [langfuse_handler]})
```

**Application metrics:** Prometheus + Grafana (standard FastAPI instrumentation via `prometheus-fastapi-instrumentator`)

**Logging:** Structured JSON logs (structlog) → shipped to Loki or CloudWatch

**Alerting:** Grafana alerts on p95 latency, RQ queue depth, scrape error rate

---

## 4. Data Flow Diagrams

### 4.1 URL Ingestion Flow

```
User                  Frontend         FastAPI          RQ Worker           Storage
 │                       │                │                   │               │
 │  Submit URL           │                │                   │               │
 │──────────────────────►│                │                   │               │
 │                       │  POST /ingest  │                   │               │
 │                       │───────────────►│                   │               │
 │                       │                │  enqueue task     │               │
 │                       │                │──────────────────►│               │
 │                       │  { job_id }    │                   │               │
 │                       │◄───────────────│                   │               │
 │  Show job progress     │                │                   │               │
 │◄──────────────────────│                │  ScraperRouter    │               │
 │                       │                │  .scrape(url)     │               │
 │                       │                │                   │  HTTP/Browser │
 │                       │                │                   │──────────────►│
 │                       │                │                   │  raw HTML     │
 │                       │                │                   │◄──────────────│
 │                       │                │                   │               │
 │                       │                │                   │  parse+embed  │
 │                       │                │                   │──────────────►│
 │                       │                │                   │  PostgreSQL   │
 │                       │                │                   │  (pgvector)   │
 │                       │                │  job status=DONE  │               │
 │                       │  SSE update    │◄──────────────────│               │
 │◄──────────────────────│◄───────────────│                   │               │
```

### 4.2 Chat Flow

```
User             Frontend         FastAPI (WS)      LangChain        PostgreSQL
 │                  │                  │                 │            (pgvector)
 │  Ask question    │                  │                 │                  │
 │─────────────────►│                  │                 │                  │
 │                  │  WS message      │                 │                  │
 │                  │─────────────────►│                 │                  │
 │                  │                  │  embed question │                  │
 │                  │                  │────────────────►│                  │
 │                  │                  │                 │  HNSW MMR query  │
 │                  │                  │                 │─────────────────►│
 │                  │                  │                 │  top-k chunks    │
 │                  │                  │                 │◄─────────────────│
 │                  │                  │                 │  build prompt    │
 │                  │                  │                 │  stream → OpenAI │
 │                  │  stream tokens   │  stream tokens  │                  │
 │◄─────────────────│◄─────────────────│◄────────────────│                  │
```

---

## 5. Technology Stack Summary

| Layer | Technology | Version |
|---|---|---|
| Frontend | Plain HTML + Vanilla JS + Tailwind CSS (CDN) | — |
| API Server | FastAPI + Uvicorn + Gunicorn | FastAPI 0.115+ |
| Task Queue | Redis RQ | rq 1.x |
| Scraping (Tier 1) | httpx + BeautifulSoup4 | async |
| Scraping (Tier 2) | Playwright (async) + Scrapy | latest |
| Scraping (Tier 3) | Firecrawl SDK / BrightData SDK / Zyte API | — |
| LLM Orchestration | LangChain (LCEL) | 0.3+ |
| LLM Providers | OpenAI (gpt-4o) + Anthropic (claude-sonnet-4-6) | — |
| Embeddings | OpenAI text-embedding-3-small (fallback: Voyage AI) | — |
| Vector Store | pgvector extension on PostgreSQL 16 | 0.7+ |
| LangChain PGVector | langchain_postgres | 0.0.x |
| Relational DB | PostgreSQL 16 + SQLAlchemy async | — |
| Migrations | Alembic | — |
| Observability | Langfuse (self-hosted) + Prometheus + Grafana | — |
| Hosting | Render (Web Services + Background Workers) | — |
| Containerisation | Docker + Docker Compose (local dev only) | — |
| CI/CD | GitHub Actions | — |

---

## 6. Deployment Architecture

### Production — Render

Render handles TLS, reverse proxying, and health checks automatically. No Nginx, no Traefik, no container orchestration config needed.

```
Render Platform
    │
    ├──► Web Service: FastAPI  (serves frontend static files + API)
    │    Start command: uvicorn main:app --host 0.0.0.0 --port $PORT
    │    Frontend HTML/JS served via FastAPI StaticFiles mount
    │    Auto-scaling: 1–N instances based on traffic
    │
    ├──► Background Worker: RQ scrape (4 threads)
    │    Start command: rq worker scrape --with-scheduler
    │
    ├──► Background Worker: RQ embed + file (2 threads each)
    │    Start command: rq worker embed file
    │
    ├──► Render Redis          (managed, queue + cache)
    │
    └──► Render PostgreSQL     (managed, pgvector extension enabled)
```

**Render service map (`render.yaml`):**
```yaml
services:
  - type: web
    name: reviewlens-api
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn backend.main:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: reviewlens-db
          property: connectionString
      - key: REDIS_URL
        fromService:
          name: reviewlens-redis
          property: connectionString

  - type: worker
    name: reviewlens-worker-scrape
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: rq worker scrape --with-scheduler

  - type: worker
    name: reviewlens-worker-embed
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: rq worker embed file

databases:
  - name: reviewlens-db
    plan: standard
    postgresMajorVersion: 16

  - name: reviewlens-redis
    plan: starter
```

**pgvector on Render PostgreSQL:** The Render managed Postgres supports extensions via `CREATE EXTENSION IF NOT EXISTS vector;` in the first Alembic migration — no extra config needed.

### Local Development — Docker Compose

`docker-compose.yml` runs the full stack locally (FastAPI + RQ workers + PostgreSQL + Redis + Langfuse). No Nginx or Traefik needed locally either — FastAPI's Uvicorn serves directly on `localhost:8000`.

```yaml
# docker-compose.yml (abbreviated)
services:
  api:       # uvicorn backend.main:app --reload
  worker:    # rq worker scrape embed file
  db:        # postgres:16 with pgvector
  redis:     # redis:7-alpine
  langfuse:  # self-hosted observability
```

---

## 7. Security Considerations

Requirement #1 calls for "secure access" while Requirement #2 specifies no authentication. These are reconciled by treating security as *infrastructure hardening of a public endpoint* rather than identity-based access control. Concretely:

| Risk | Mitigation |
|---|---|
| Scraping abuse (someone uses us to hammer sites) | Rate limit `/ingest` to 10 req/min/IP via `slowapi`; domain allowlist configurable |
| Prompt injection via review content | System prompt instructs model to ignore instructions in context; input sanitised before embedding |
| SSRF via URL input | Validate URL scheme (http/https only); block private IP ranges (10.x, 192.168.x, 169.254.x) |
| File upload abuse | Validate MIME type server-side; max file size 50 MB; virus scan with ClamAV (optional) |
| XSS | React escapes by default; DOMPurify for any rendered HTML review content |
| SQL injection | SQLAlchemy parameterised queries only |
| Sensitive keys | All API keys in environment variables / Docker secrets; never committed |
| DoS on LLM | Per-session token budget enforced in chain config; RQ worker concurrency caps |

---

## 8. Phased Implementation Plan

### Phase 1 — Core Foundation (Weeks 1–3)
- [ ] Project scaffold: monorepo with `frontend/`, `backend/`, `docker/`
- [ ] FastAPI app with health endpoint, CORS, rate limiting
- [ ] PostgreSQL schema + Alembic migrations (including `CREATE EXTENSION vector`)
- [ ] File ingestion: CSV/JSON upload, normalise, store in Postgres
- [ ] pgvector embedding pipeline via `langchain_postgres.PGVector` (RQ job)
- [ ] Basic chat endpoint (no streaming) with LangChain RAG
- [ ] Plain HTML/JS frontend: file upload + basic chat UI (served via FastAPI StaticFiles)
- [ ] Docker Compose for full local stack

### Phase 2 — Scraping (Weeks 4–5)
- [ ] Tier 1 scrapers: G2, Capterra (BeautifulSoup)
- [ ] Tier 2: Playwright integration + browser pool
- [ ] Tier 3: Firecrawl SDK integration (easiest managed provider to start)
- [ ] Amazon + Google Maps adapters (Tier 3)
- [ ] Scrapy spider for paginated crawls
- [ ] BrightData / Zyte as second/third fallback providers
- [ ] Ingestion job status polling (SSE)

### Phase 3 — Production Hardening (Weeks 6–7)
- [ ] Streaming chat (WebSocket token streaming)
- [ ] OpenAI primary + Anthropic fallback with `with_fallbacks()`
- [ ] HNSW index tuning (`m`, `ef_construction`, `ef_search`) under realistic data volume
- [ ] Langfuse integration + Prometheus metrics
- [ ] Grafana dashboards
- [ ] `render.yaml` service definitions + environment variable wiring

### Phase 4 — UX Polish + Observability (Week 8)
- [ ] Review browser with filters (platform, rating, date)
- [ ] Chat history persistence
- [ ] Model selector in UI
- [ ] Langfuse prompt management
- [ ] Load testing (Locust) + performance tuning
- [ ] Production Docker Compose with resource limits

---

## 9. Open Questions / Decisions Needed

1. **Amazon**: Their TOS prohibits scraping. To be addressed in a later phase — Amazon ingestion will not be in the initial release.
~~**Firecrawl vs BrightData**~~ — **Decided:** BrightData is the primary Tier 3 provider. Firecrawl will be evaluated as an alternative based on results.

~~**Google Maps**~~ — **Decided:** use the Places API (requires Google Cloud billing). More reliable and legally cleaner than scraping.

~~**Embedding cache**~~ — **Decided:** not needed. Data volume is small enough that re-embedding on ingest is acceptable; no Redis caching layer for embeddings.

~~**Multi-tenancy scope**~~ — **Decided:** all users share a single namespace. No session isolation needed. `source_id` filtering on `review_chunks` scopes chat to the selected source.

~~**pgvector scale ceiling**~~ — **Decided:** pgvector is sufficient for this project's scale. No migration path to a dedicated vector DB needed.
