# SpiderMind — Phase 1 crawler

SpiderMind is an incremental AI research project. Its long-term goal is to investigate a question by finding sources, following citations, indexing knowledge, checking claims, and producing citation-backed reports with a navigable source graph. **This repository currently implements only the crawler foundation.** There are no LLMs, embeddings, RAG, agents, or frontend.

## What works today

- Asynchronous FastAPI crawl jobs with status and counters.
- Breadth-first URL frontier with a separate scheduling interface and URL deduplication.
- HTTP/HTTPS normalization, same-domain default scope, optional external-domain traversal, depth and page limits.
- Async HTTP fetching with timeouts, bounded streaming responses, redirects, content-type checks, and limited transient retries.
- Per-domain request spacing shared across jobs; cached, user-agent-aware robots.txt decisions.
- Title, description, canonical URL, readable text, and internal/external links.
- SHA-256 content deduplication. Duplicate pages retain metadata and a pointer to their original, without a second text copy.
- SQLite persistence, JSON logs with job IDs, readiness checks, and tests using controlled HTTP responses.

## Architecture

```text
POST /api/v1/crawl ──> CrawlService ──> background task
                                         │
                                         v
                                  Crawler loop
                              ┌──────┼────────┐
                              │      │        │
                           Frontier  Robots   Fetcher ──> Rate limiter
                              │      │        │             │
                              └──────┼────────┘          Target guard
                                     v
                              Parser + SHA-256
                                     │
                                     v
                     SQLite: jobs, pages, discovered links
```

The frontier owns ordering and URL identity; the crawler owns limits and scope. Fetching, robots policy, parsing, and persistence have separate boundaries. See [architecture.md](docs/architecture.md).

## Project structure

```text
backend/
  app/
    api/routes/      # health and crawl endpoints
    core/            # configuration and JSON logging
    crawler/         # frontier, normalization, safety, fetching, robots, parser, orchestration
    db/              # SQLAlchemy models, engine, repository
    schemas/         # Pydantic API contracts
    services/        # crawl job lifecycle
    main.py
  tests/             # component and API/integration tests
  requirements.txt
docs/architecture.md
pyproject.toml
```

## Set up on Windows PowerShell

From the repository root, with Python 3.11 or newer installed:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

The default SQLite file is `spidermind.db` in the working directory. Set `SPIDERMIND_DATABASE_URL` to another `sqlite+aiosqlite:///...` URL if needed. Other environment settings include `SPIDERMIND_USER_AGENT`, `SPIDERMIND_REQUEST_TIMEOUT_SECONDS`, `SPIDERMIND_DOMAIN_DELAY_SECONDS`, `SPIDERMIND_MAX_REDIRECTS`, and `SPIDERMIND_MAX_RETRIES`. Crawling defaults to one second between requests to the same domain.

## API

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
$body = @{
  start_url = "https://example.com"
  max_depth = 2
  max_pages = 25
  timeout = 120
  max_content_size = 2000000
  allow_external_domains = $false
  allowed_domains = @()
} | ConvertTo-Json
$job = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/crawl -ContentType application/json -Body $body
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/crawl/$($job.job_id)"
```

`POST` returns HTTP 202 with `job_id` and `PENDING`. `GET` returns `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, or `CANCELLED`, plus `pages_discovered`, `pages_crawled`, `pages_failed`, `pages_skipped`, and an optional error. A completed job can contain failed pages. Invalid or unsafe start URLs return HTTP 422.

`pages_discovered` counts unique normalized URLs, including out-of-scope references. `pages_crawled` counts useful unique-content pages. `pages_failed` counts page attempts that failed. `pages_skipped` counts out-of-scope/depth links, robots-denied pages, duplicate-content pages, and in-scope pages left after `max_pages`. The `max_pages` cap applies to popped frontier items (including denied or failed attempts), not to discovered links.

`allowed_domains` is an optional exact-host allowlist and must include the start host when set. `allow_external_domains` must be `true` to traverse hosts other than the start host; both settings apply together. External links are always stored in `discovered_links` even when not traversed.

## Tests and checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m ruff format --check backend
.\.venv\Scripts\python.exe -m compileall -q backend\app
```

The suite uses HTTPX mock transports and temporary SQLite databases, with no dependency on public websites.

## Current limits and roadmap

Jobs run as in-process tasks. Restarting the API marks unfinished jobs failed; there is no durable worker or distributed scheduling. Tables are created on startup, with no schema migrations yet. SQLite is suited to local development and modest crawl volume. The crawler does not run JavaScript, authenticate to sites, enforce site-specific crawl-delay directives, or parse sitemaps. Robots retrieval fails closed when unavailable, except 404/410 (treated as no policy). The URL guard rejects local/private and non-global resolved IPs, validates every redirect hop, and disables environment proxies. DNS validation and connection resolution are separate, so DNS rebinding between them remains a known SSRF limit; production exposure to untrusted users needs connection-level address pinning and network egress controls.

Phase 2 can replace FIFO frontier ordering with relevance scores, add durable workers, migrations, and source graph processing while retaining the parser, fetcher, and persisted link records. No Phase 2 AI features are present now.
