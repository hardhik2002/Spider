# SpiderMind — Phase 1 and 2 crawler

SpiderMind is an incremental AI research project. Its long-term goal is to investigate a question by finding sources, following citations, indexing knowledge, checking claims, and producing citation-backed reports with a navigable source graph. **The current Phase 2 application chooses which discovered URL to crawl next using local semantic embeddings. It does not yet perform autonomous research or generate answers.** There are no LLMs, RAG pipelines, agents, vector databases, or frontend.

## Phase 1 foundation

- Asynchronous FastAPI crawl jobs with status and counters.
- Breadth-first URL frontier with a separate scheduling interface and URL deduplication.
- HTTP/HTTPS normalization, same-domain default scope, optional external-domain traversal, depth and page limits.
- Async HTTP fetching with timeouts, bounded streaming responses, redirects, content-type checks, and limited transient retries.
- Per-domain request spacing shared across jobs; cached, user-agent-aware robots.txt decisions.
- Title, description, canonical URL, readable text, and internal/external links.
- SHA-256 content deduplication. Duplicate pages retain metadata and a pointer to their original, without a second text copy.
- SQLite persistence, JSON logs with job IDs, readiness checks, and tests using controlled HTTP responses.

## Phase 2 intelligent crawling

- `crawl_mode: "fifo"` remains the default for existing clients. `crawl_mode: "intelligent"` requires `research_query`.
- One query embedding per job; batched candidate embeddings using a replaceable provider. The default is locally run `BAAI/bge-m3` through Sentence Transformers.
- Candidate text combines target URL, anchor, source title, and a short nearby text window. Raw page bodies are not sent as candidate context.
- Cosine similarity ranks eligible links. `priority_score = relevance_score - depth × depth_penalty` (default `0.02`). Cosine values are similarities in `[-1, 1]`, **not probabilities**.
- An optional `min_relevance_score` excludes lower-scoring links while retaining them in the database. Deterministic exploration periodically pops the lowest-priority *eligible* link (default every tenth pop). A configured threshold remains a hard cutoff.
- `/api/v1/crawl/{job_id}/links` exposes link context, scores, decisions, and rejection reasons with filters and pagination.
- Page-level semantic relevance and a same-budget evaluation utility compare FIFO with intelligent crawling. A small labeled fixture provides an independent relevance check.

## Architecture

```text
POST /api/v1/crawl ──> CrawlService ──> background task
                                         │
                                         v
                                  Crawler loop
                              ┌──────┼────────┐
                              │      │        │
                       FIFO/Priority Robots  Fetcher ──> Rate limiter
                              │      │        │             │
                              └──────┼────────┘          Target guard
                                     v
                          Parser + SHA-256
                                │
                   Link scheduler + semantic scorer
                                │
                       Local embedding provider
                                     │
                                     v
                     SQLite: jobs, pages, discovered links
```

The frontier owns ordering and URL identity; the scheduler owns eligibility, batch scoring, and link decisions. The crawler coordinates them without embedding mathematics. See [architecture.md](docs/architecture.md).

## Project structure

```text
backend/
  app/
    api/routes/      # health and crawl endpoints
    core/            # configuration and JSON logging
    crawler/         # frontier, normalization, safety, fetching, parser, embeddings, scoring
    db/              # SQLAlchemy models, additive migration, repository
    evaluation/      # measured comparison and controlled-site demo
    schemas/         # Pydantic API contracts
    services/        # crawl job lifecycle
    main.py
  tests/             # component and API/integration tests
  requirements.txt
  requirements-intelligent.txt
  tests/fixtures/research_site/
docs/architecture.md
docs/phase2-demo-results.json
pyproject.toml
```

## Set up on Windows PowerShell

From the repository root, with Python 3.11 or newer installed:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-intelligent.txt
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

The first intelligent crawl downloads BGE-M3 from Hugging Face and caches it locally. To download and check the model before starting the API:

```powershell
.\.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer; model = SentenceTransformer('BAAI/bge-m3'); print(model.get_embedding_dimension())"
```

This download needs internet once; embedding inference then runs locally. To run only FIFO without the model dependency, install `backend\requirements.txt` instead. The default SQLite file is `spidermind.db` in the working directory. Existing Phase 1 SQLite files receive an idempotent, additive Phase 2 schema migration on startup. Set `SPIDERMIND_DATABASE_URL` to another `sqlite+aiosqlite:///...` URL if needed. Configuration includes `SPIDERMIND_EMBEDDING_MODEL_NAME`, `SPIDERMIND_EMBEDDING_BATCH_SIZE`, `SPIDERMIND_MAX_CANDIDATE_CONTEXT_CHARS`, `SPIDERMIND_MAX_PAGE_SCORING_CHARS`, `SPIDERMIND_DEPTH_PENALTY`, `SPIDERMIND_EXPLORATION_RATE`, and `SPIDERMIND_DEFAULT_MIN_RELEVANCE_SCORE`, plus Phase 1 HTTP and rate-limit settings. Crawling defaults to one second between requests to the same domain.

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

For intelligent mode:

```powershell
$body = @{
  start_url = "https://example.com"
  research_query = "evaluation techniques for hallucinations in RAG systems"
  crawl_mode = "intelligent"
  max_depth = 3
  max_pages = 25
  min_relevance_score = 0.25
} | ConvertTo-Json
$job = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/crawl -ContentType application/json -Body $body
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/crawl/$($job.job_id)"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/crawl/$($job.job_id)/links?order=relevance&selection=selected&limit=50&offset=0"
```

The links endpoint accepts `order=discovered|relevance`, `selection=all|selected|rejected`, optional `internal=true|false`, and `limit`/`offset`. A selected link is one admitted to the frontier; links later denied by robots or left by the page cap become rejected with a reason. FIFO jobs can provide a query for page-level evaluation while retaining FIFO ordering.

`POST` returns HTTP 202 with `job_id` and `PENDING`. `GET` returns `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, or `CANCELLED`, plus `pages_discovered`, `pages_crawled`, `pages_failed`, `pages_skipped`, and an optional error. A completed job can contain failed pages. Invalid or unsafe start URLs return HTTP 422.

`pages_discovered` counts unique normalized URLs, including out-of-scope references. `pages_crawled` counts useful unique-content pages. `pages_failed` counts page attempts that failed. `pages_skipped` counts out-of-scope/depth links, robots-denied pages, duplicate-content pages, and in-scope pages left after `max_pages`. The `max_pages` cap applies to popped frontier items (including denied or failed attempts), not to discovered links.

`allowed_domains` is an optional exact-host allowlist and must include the start host when set. `allow_external_domains` must be `true` to traverse hosts other than the start host; both settings apply together. External links are always stored in `discovered_links` even when not traversed.

## Measured controlled comparison

Run the bundled site with the **real local BGE-M3 model** and no public website traffic:

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m app.evaluation.demo
```

The script creates FIFO and intelligent jobs with the same query, scope, and four-page budget in `spidermind-demo.db`, then prints status, `/links` results, database scoring counts, and evaluation metrics. The measured run in [phase2-demo-results.json](docs/phase2-demo-results.json) yielded:

| Metric | FIFO | Intelligent |
| --- | ---: | ---: |
| Relevant pages from fixture labels / 4 crawled | 1 | 3 |
| Relevant-page yield | 0.25 | 0.75 |
| Mean page cosine relevance | 0.4204 | 0.6587 |
| High-relevance hit rate (page score ≥ 0.6) | 0.25 | 0.50 |
| Crawl efficiency (labeled relevant pages / 4-page budget) | 0.25 | 0.75 |

FIFO crawled seed → About → Careers → RAG Evaluation. Intelligent crawled seed → Hallucination → RAG Evaluation → Embeddings. This is evidence for this small fixture, not a general performance claim. Evaluation definitions and a CLI for comparing any two completed jobs are in [compare.py](backend/app/evaluation/compare.py). For example:

```powershell
.\.venv\Scripts\python.exe -m app.evaluation.compare --fifo-job <fifo-id> --intelligent-job <intelligent-id> --labels-file backend\tests\fixtures\research_site\labels.json
```

## Tests and checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m ruff format --check backend
.\.venv\Scripts\python.exe -m compileall -q backend\app
```

The suite uses HTTPX mock transports and temporary SQLite databases, with no dependency on public websites.

## Current limits and roadmap

Jobs run as in-process tasks. Restarting the API marks unfinished jobs failed; there is no durable worker or distributed scheduling. The SQLite migration only adds Phase 2 columns; it is not a general migration framework. BGE-M3 has a substantial one-time download, memory footprint, and CPU cost. A threshold may exclude genuinely useful low-scoring pages, and link context can be sparse or misleading. The crawler does not run JavaScript, authenticate to sites, enforce site-specific crawl-delay directives, or parse sitemaps. Robots retrieval fails closed when unavailable, except 404/410. The URL guard rejects local/private and non-global resolved IPs, validates every redirect hop, and disables environment proxies. DNS validation and connection resolution are separate, so DNS rebinding remains a known SSRF limit; production exposure to untrusted users needs connection-level address pinning and network egress controls.

Phase 3 can add search and research planning by feeding seed URLs and objectives into the existing crawl API. It should not require a rewrite of the Phase 2 frontier or scorer. Answer generation, RAG, and knowledge graph engines remain future work.
