# SpiderMind project context

_Updated 29 October 2026 for Phase 8A. This describes the current working tree. It is a guide to the implementation, not a replacement for the source code or the recorded result JSON._

## 1. Purpose and current boundary

SpiderMind has a Python 3.11+ FastAPI research backend and a Phase 8A React/TypeScript graph client. The backend accepts a URL or research question, crawls public HTML, plans searches, indexes passages, runs a bounded research loop, produces a claim/evidence ledger, and materializes a deterministic citation graph. The frontend explores that graph without changing evidence semantics. There is no authentication or final answer writer. The API advertises version `0.7.0` in `backend/app/main.py`; `pyproject.toml` still labels the Python package `0.1.0` and describes the original Phase 1 foundation.

The phases build on one another:

| Phase | Responsibility | Main output |
| --- | --- | --- |
| 1 | Safe, bounded, persistent FIFO web crawl | Crawl jobs, pages, discovered links |
| 2 | Query aware link/page scoring and priority crawl | Relevance scores and crawl decisions |
| 3 | LLM research plan, web search, seed selection, and crawls | Research job with source lineage |
| 4 | Passage indexing and dense/lexical/hybrid retrieval | Documents, chunks, retrieval results |
| 5 | Bounded iterative acquisition of missing evidence | Agent run, gaps, actions, checkpoints |
| 6 | Atomic claim extraction and evidence verification | Claim relations, exact citations, ledger |
| 7 | Deterministic projection, validation, traversal, analytics, export | Versioned knowledge/citation graph |
| 8A | Interactive, bounded visual exploration of the Phase 7 graph | Spider-centered React graph UI |

### End-to-end flow

```text
question -> validated plan -> deduplicated web queries -> public search results
         -> semantic seed ranking -> intelligent crawls -> SQLite pages
         -> token chunks -> SQLite/FTS5 + Qdrant -> hybrid retrieval
         -> optional agent gap loop -> claim extraction -> NLI/adjudication
         -> citation checks + independent source groups -> evidence ledger
         -> versioned SQLite graph -> validation/traversal/analytics/JSON export
         -> bounded Phase 7 API client -> React Flow spider graph and inspector
```

Phase 1/2 crawls can be used directly without a research job. Phases 4–6 consume the persisted research corpus. Phase 6 retrieves within that indexed corpus; it does not launch new web searches or crawls. Phase 7 projects only persisted Phase 1–6 relationships and makes no new LLM decisions.
Phase 8A reads Phase 7 records and does not create claims, change confidence, or generate final answers.

## 2. Runtime architecture

`backend/app/main.py` creates the FastAPI app and owns its lifespan. Startup configures JSON logging, creates/migrates the SQLite schema, opens one HTTPX client with redirects and environment proxies disabled, builds all services, opens a LangGraph SQLite checkpointer, recovers interrupted jobs, and attaches services to `app.state`. Shutdown cancels background tasks, closes the vector index and HTTP client, and disposes the SQLAlchemy engine. `create_app(...)` accepts injectable planner, search, embeddings, vectors, reranker, tokenizer, agent LLM interfaces, and evidence classifiers/LLM interfaces; tests use these seams.

The HTTP layer under `app/api/routes` performs request parsing, status lookup, filtering, and serialization. `app/services` owns crawl/research orchestration. `app/crawler`, `app/rag`, `app/agent`, and `app/evidence` implement the phase logic. SQLAlchemy models live in `app/db/models.py` and the phase-specific `models.py` files. All main job types run as in-process `asyncio` tasks. The agent uses LangGraph checkpoints; the other job types persist their status/progress in SQLite.

`frontend/` is a separate Vite client. Its central API module maps the actual Phase 7 DTOs; a hook coordinates snapshot loading, polling, bounded `/subgraph` expansion, claim inspection, contradictions, and directed paths. A deterministic layout adapter converts graph records into customized React Flow nodes and edges. The spider is a decorative asset, not a graph node. Vite proxies local API requests to FastAPI, so the backend needs no CORS change for the documented development setup. The frontend does not use LLMs or mock production graph data.

The shared embedding provider is local Sentence Transformers `BAAI/bge-m3`. Phase 3 planning and structured Phase 5/6 LLM calls use local Ollama, default `Qwen3:latest`. Search uses `ddgs`. Phase 4 vectors use local Qdrant; lexical search uses SQLite FTS5 or a local BM25 fallback. Reranking defaults to the local `BAAI/bge-reranker-v2-m3` cross encoder on CPU. Phase 6 NLI defaults to `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` on CPU. Model loading/downloading can dominate the first request; no external hosted LLM API is wired in the default app.

### Storage and identity

The default relational URL is `sqlite+aiosqlite:///./spidermind.db`, relative to the process working directory. Qdrant defaults to `data/qdrant`; checkpoints default to `data/langgraph-checkpoints.sqlite`. These are independent stores. Research and crawl provenance is stored in SQL; vectors are in Qdrant; graph routing state is in the checkpoint database. URLs, canonical URLs, content hashes, chunk hashes, research IDs, crawl IDs, source groups, and stable claim keys each serve distinct identity purposes. A search result is not automatically a crawled page, and a crawled descendant page is attributed to the seed crawl job rather than the original search result.

## 3. API contract

All job creation endpoints below return HTTP 202. IDs in paths are UUIDs. Unknown IDs return 404. Pydantic limits yield 422. The exact response shapes are defined by the route files and their referenced schemas.

| Method and path | Purpose |
| --- | --- |
| `GET /health` | Process health. |
| `GET /ready` | Database readiness. |
| `POST /api/v1/crawl` | Start FIFO or intelligent crawl. |
| `GET /api/v1/crawl/{job_id}` | Crawl status, counters, scoring metrics, timings. |
| `GET /api/v1/crawl/{job_id}/links` | Inspect link context, scores, selection/rejection; `order`, `selection`, `internal`, `limit`, `offset` filters. |
| `POST /api/v1/research` | Start a planned research job. |
| `GET /api/v1/research/{job_id}` | Research status and counters. |
| `GET /api/v1/research/{job_id}/plan` | Validated plan and subquestion state. |
| `GET /api/v1/research/{job_id}/searches` | Query/result associations and search outcomes. |
| `GET /api/v1/research/{job_id}/sources` | Seed decisions, crawls, and page provenance. |
| `POST /api/v1/research/{job_id}/index` | Start or reuse active index job. |
| `GET /api/v1/research/{job_id}/index` | Latest indexing job, counts, document outcomes. |
| `GET /api/v1/research/{job_id}/index/consistency` | Compare SQL, FTS, and Qdrant records. |
| `POST /api/v1/research/{job_id}/retrieve` | Dense, lexical, or hybrid passage retrieval. |
| `POST /api/v1/research/{job_id}/agent` | Start bounded Phase 5 research loop. |
| `GET /api/v1/research/{job_id}/agent/{run_id}` | Agent state, budget, counters, stop reason. |
| `GET /api/v1/research/{job_id}/agent/{run_id}/iterations` | Per-iteration trace and actions. |
| `GET /api/v1/research/{job_id}/agent/{run_id}/gaps` | Open/resolved research gaps. |
| `POST /api/v1/research/{job_id}/agent/{run_id}/cancel` | Request cancellation between graph nodes. |
| `POST /api/v1/research/{research_job_id}/evidence` | Start/reuse an evidence job, optionally tied to an agent run. |
| `GET /api/v1/research/{research_job_id}/evidence/{job_id}` | Evidence stage, counters, timings. |
| `GET /api/v1/research/{research_job_id}/evidence/{job_id}/claims` | Filter/order claim summaries. |
| `GET /api/v1/research/{research_job_id}/evidence/{job_id}/claims/{claim_id}` | Claim, origins, relations, citations, rule metadata. |
| `GET /api/v1/research/{research_job_id}/evidence/{job_id}/contradictions` | Contradictory evidence view. |
| `GET /api/v1/research/{research_job_id}/evidence/{job_id}/ledger` | Structured claim/evidence ledger. |
| `POST /api/v1/research/{research_job_id}/graph` | Build/reuse graph snapshot for an evidence job. |
| `GET /api/v1/research/{research_job_id}/graph/{snapshot_id}` | Build/validation status and counts. |
| `GET /api/v1/research/{research_job_id}/graph/{snapshot_id}/nodes` | Filtered/paged compact nodes. |
| `GET /api/v1/research/{research_job_id}/graph/{snapshot_id}/nodes/{node_id}` | Node detail; optional chunk text. |
| `GET /api/v1/research/{research_job_id}/graph/{snapshot_id}/edges` | Filtered/paged semantic edges. |
| `GET /api/v1/research/{research_job_id}/graph/{snapshot_id}/subgraph` | Bounded directional neighborhood. |
| `GET /api/v1/research/{research_job_id}/graph/{snapshot_id}/path` | Bounded shortest directed provenance paths. |
| `GET /api/v1/research/{research_job_id}/graph/{snapshot_id}/claims/{claim_id}` | Complete claim neighborhood. |
| `GET /api/v1/research/{research_job_id}/graph/{snapshot_id}/contradictions` | Contested/contradicted claim views. |
| `GET /api/v1/research/{research_job_id}/graph/{snapshot_id}/summary` | Structural graph analytics. |
| `GET /api/v1/research/{research_job_id}/graph/{snapshot_id}/consistency` | Compare graph with authoritative evidence. |
| `GET /api/v1/research/{research_job_id}/graph/{snapshot_id}/export` | Deterministically ordered JSON export. |

Important request models:

- `CrawlRequest`: normalized `start_url`; depth 0–10, pages 1–500, timeout, response size, exact-host allowlist, external-domain opt-in, `fifo`/`intelligent`, optional query and scoring controls. Intelligent mode requires a query; a relevance threshold requires intelligent mode. `allowed_domains` must include the start host if supplied.
- `ResearchRequest`: question (8–2000 characters and at least two words), maximum subquestions, queries, results, seeds, per-subquestion/total page budgets, and depth. `ResearchPlan` is typed and validates unique subquestion IDs and request limits.
- `RetrievalRequest`: query, `dense`/`lexical`/`hybrid`, stage-specific top-K overrides, rerank and neighbor flags, and optional subquestion, source domain, document, or crawl filters. Returned results include text, URL, IDs, source metadata, rank paths, raw scores, neighbor context, and timing.
- `AgentRequest`: iteration/query/seed/page/runtime budgets, deterministic evidence minimums, retrieval depth, rerank and bounded assessment settings.
- `EvidenceRequest`: optional agent run, subquestion/claim/candidate/citation limits, runtime, NLI/adjudication/counterquery/rerank switches, extraction pack limits. The subquestion × claim × candidate product is capped at 2,000, and final citations cannot exceed candidate count.
- `GraphBuildRequest`: evidence-job UUID and optional uncited-relation inclusion. Graph status is asynchronous; validated snapshots can be queried once completed.

### Typical local sequence

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-graph.txt
ollama pull Qwen3:latest
$env:PYTHONPATH = 'backend'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

The requirements files form a phase chain: `backend/requirements.txt`, then `requirements-intelligent.txt`, `requirements-research.txt`, `requirements-rag.txt`, `requirements-agent.txt`, `requirements-evidence.txt`, and `requirements-graph.txt` (no additional package). The first model run may need downloads. Start a research job, poll it, post `/index`, poll indexing, optionally post `/agent`, post `/evidence`, and then post `/graph`. The README has full PowerShell request examples. A FIFO crawl can be posted directly to `/api/v1/crawl`.

**Current startup dependency:** `app/main.py` imports the agent, evidence, and graph modules at import time. As written, the full API needs the Phase 7 requirements chain installed even if the only requested operation is FIFO crawling. The older README suggestion to install only `requirements.txt` for a FIFO-only server does not match this startup path.

## 4. Detailed processing rules

### Crawl: Phases 1 and 2

`CrawlService` validates a public start target, creates a persistent job, and launches `Crawler.run`. The crawler uses FIFO `Frontier` or `PriorityFrontier`, `LinkScheduler`, `RobotsManager`, `Fetcher`, `DomainRateLimiter`, `parse_page`, and `ContentDeduplicator`. URLs are normalized to HTTP/HTTPS with fragment removal. Target validation resolves DNS and rejects private/reserved addresses; redirects are checked for scope and robots permission. Fetching streams a bounded HTML response with timeouts and limited retries. The parser extracts readable text, title, description, canonical URL, and internal/external link context. SHA-256 of extracted text detects duplicates within a crawl job. Duplicate rows retain metadata but do not store a second body.

The frontier owns URL uniqueness/order; the scheduler owns eligibility, scope, relevance scoring, and rejection reasons. Intelligent mode embeds the query once, batches bounded candidate representations (URL, anchor, source title, nearby text), computes cosine similarity, and sets `priority = relevance - depth × depth_penalty`. A hard minimum relevance threshold excludes links; deterministic exploration occasionally pops a low-priority eligible link. FIFO can still score pages if supplied a query. `max_pages` caps popped frontier attempts, including skipped/failed pages. `pages_discovered` includes normalized out-of-scope discoveries; `pages_crawled` counts useful unique-content pages. A completed job may contain failed pages.

### Research planning and source collection: Phase 3

`ResearchService` asks `OllamaResearchPlanner` for typed JSON from the original question alone, with one schema repair attempt. It deduplicates query strings across subquestions, runs bounded concurrent `ddgs` searches, records result occurrences and query/subquestion links, and validates public URLs before seed selection. Search results are ranked for each subquestion using a blend of normalized cosine similarity and reciprocal log search rank: `semantic_weight × ((cosine + 1)/2) + (1 - semantic_weight) × (1/log2(rank + 1))`. Selection is deterministic, with a per-domain cap and persisted rejection reasons. Chosen seeds launch intelligent crawls with the subquestion text as the crawl query. Crawls run sequentially for a shared page budget and reuse already crawled URLs within one research run. Individual search/crawl failures can yield `partial`; complete failure yields `failed`. A restart marks unfinished research jobs failed.

### Index and retrieval: Phase 4

`IndexingService` selects completed, nonduplicate text pages from selected research seed crawls. `chunk_text` uses a BGE tokenizer and heading/paragraph/sentence boundaries with target/max/min/overlap token limits. SQL stores `KnowledgeDocument`, canonical `KnowledgeChunk`, and `KnowledgeChunkSource` occurrences/adjacency. Identical passage representations in one research job share a chunk, while each document occurrence remains linked. The indexer embeds chunks into a research-filtered Qdrant collection, using stable UUID5 point IDs, and indexes title/heading/text with an external-content FTS5 table and triggers. It skips unchanged documents, rebuilds changed content or changed model/profile, repairs missing vectors, and prunes orphan chunks/points. `IndexJob` and per-document statuses record progress. Interrupted index jobs become `PARTIAL` and can be retried. SQL, FTS, and Qdrant writes are not atomic as a unit; `/index/consistency` diagnoses drift.

`RetrievalService` first builds an allow set of active chunk sources for the research job and request filters. Dense search embeds the query and searches Qdrant; lexical search safely quotes FTS tokens (or computes fallback BM25). Weighted reciprocal rank fusion merges unique IDs. The optional cross encoder reranks a bounded fused set; a per-document cap is applied to final results. Optional adjacent passages are returned as context and do not count as independent retrieved hits. Cosine, BM25, fusion, and reranker scores are ranking values, not truth probabilities.

### Research agent: Phase 5

`AgentService` compiles a LangGraph: initialize → retrieve → assess → update gaps → check stop → select gap → generate queries → search → select seeds → crawl → index → advance → retrieve, with conditional routes to finalize. `ResearchAgentState` holds bounded IDs, counters, and evidence snapshots; full passages stay in SQL. `AsyncSqliteSaver` checkpoints graph state under run UUID. The agent retrieves for each planned subquestion, checks minimum distinct chunk and document counts, and asks a structured Ollama assessor to rate coverage. A model claim of sufficiency cannot override the deterministic minimum. Missing aspects become prioritized gap rows. Queries are normalized/deduplicated against Phase 3 and earlier Phase 5 queries. Search, seed selection, crawl, and indexing reuse prior phase services. Action rows and domain records are idempotency boundaries on resume.

Stop rules include sufficient evidence for all subquestions with no high-priority gap, cancellation, runtime, iteration/query/seed/page budgets, no actionable gaps, and stagnation. Page budget counts attempts, not successful pages. Graph node traces include timing and budget snapshots. The assessor receives a bounded JSON encoded evidence envelope marked `UNTRUSTED_EVIDENCE`; no model reasoning trace is exposed by the API. An agent run collects evidence and does not write final answers.

### Evidence engine: Phase 6

`EvidenceEngineService` runs sequentially under a work lock. It can start from an indexed research job or reference an agent run. An input fingerprint over request and indexed evidence makes unchanged reruns reuse a job; changed inputs produce a new job. Extraction prompts carry bounded, cleaned passages with chunk IDs inside untrusted envelopes and demand atomic typed claims. A deterministic overlap guard rejects unsupported extracted text. Whitespace/case normalization and exact matching merge obvious duplicates; semantic duplicate candidates need BGE similarity, qualifier/polarity safety, and structured equivalence approval. A stable claim hash links repeats across jobs while each job has its own claim history.

Each claim searches the whole indexed research corpus using Phase 4 retrieval and optional counterqueries. Local NLI classifies premise=evidence and hypothesis=claim in batches, recording entailment/neutral/contradiction scores. Ambiguous, numeric, temporal, comparative, or conflicting cases can go to structured Qwen3 adjudication. Scope/qualifier guards prevent obvious different years, versions, datasets, or conditions from being treated as direct support/contradiction. Each evaluated claim/chunk/document pair is a relation row. Direct support and contradiction citation candidates are reduced to a qualifying sentence or adjacent pair; offsets and exact text must match the persisted chunk. Neutral text is not a supporting citation. Canonical URL or exact content hash groups mirrored documents as one independent source.

The deterministic status rule counts **valid-cited independent source groups**: two or more supporting groups without contradiction → `CORROBORATED`/`HIGH`; one → `SUPPORTED`/`MEDIUM`; partial support alone → `PARTIALLY_SUPPORTED`/`LOW`; support plus contradiction → `CONTESTED`/`UNRESOLVED`; contradiction alone → `CONTRADICTED`/`UNRESOLVED`; otherwise → `INSUFFICIENT_EVIDENCE`/`UNRESOLVED`. An uncited strong relation prevents a resolved status unless both support and contradiction are already validly cited. The confidence tier is evidence strength, not calibrated truth probability. The ledger exposes origins, relation decisions, citations, source groups, and rule metadata; it does not compose an answer.

### Knowledge and citation graph: Phase 7

`GraphBuilder` loads one finished Phase 6 evidence job and its Phase 3/4 provenance, then creates deterministic nodes and edges. SQLite graph tables are a derived, versioned view; Phase 1–6 rows remain authoritative. Node IDs have the form `research:<id>`, `subquestion:<id>`, `claim:<id>`, `citation:<id>`, `evidence_chunk:<id>`, `document:<id>`, and `source_group:<id>`. Edge IDs are SHA-256 hashes of edge type, endpoints, and source-row identity. The graph is a directed multigraph, so claim origin and verified support remain distinct even for the same claim/chunk pair. The builder maps only persisted direct support, partial support, contradiction, citation, chunk occurrence, and source-group membership relationships. It does not infer claim-to-claim relations or call an LLM.

`GraphService` fingerprints authoritative rows and the uncited-relation option. An unchanged completed graph reuses its snapshot; changed evidence produces a new snapshot/content hash. `SQLiteGraphStore` writes a snapshot's nodes/edges in one transaction. `GraphValidator` checks expected entities/edges, endpoints, exact citation offsets, claim status/confidence, research-job scope, source occurrences/groups, and provenance paths. Invalid snapshots remain visible through status/consistency but cannot be queried through graph data APIs; completed graph reads also verify the stored content hash. `GraphProjection` supports bounded directional neighborhoods, shortest paths, and weak connected components without NetworkX. `GraphQueryService`, `GraphAnalytics`, and `GraphExporter` provide filters, claim/contradiction views, structural counts, and sorted JSON. Normal graph responses omit full chunk bodies; node detail can fetch authoritative text on request, and an explicit setting can add 1,200-character excerpts to export. GraphML is not implemented. The separate Phase 8A frontend consumes these APIs.

The controlled fixture records 24 nodes, 42 edges, 100% exact-set accuracy on five labeled graph queries, complete claim/citation/source-group path coverage, zero orphan edges, and zero cross-job leakage. Those are fixture properties, not factual accuracy claims. A copied live Phase 6 database produced 12 nodes and 19 edges with `VALID` integrity status. See `docs/phase7-graph-results.json` and `docs/phase7-live-smoke-results.json`.

## 5. Data model and migration

SQLAlchemy's `Base.metadata.create_all` and `migrate_sqlite` create missing tables and add columns to older SQLite files. The migration is additive and idempotent, installs FTS5 triggers when available, and sets `PRAGMA user_version = 7`; graph schema version 1 is separate. It is not an Alembic migration history. FTS5 absence switches lexical retrieval to local BM25.

| Data area | Tables and relationships |
| --- | --- |
| Crawl | `crawl_jobs` owns budgets/status/counters; `crawled_pages` has one row per attempted normalized URL with content/hash/status; `discovered_links` records source/target/context, scores, selection, and rejection. |
| Research | `research_jobs` owns question/plan/status; `research_subquestions`; `research_search_queries` (including Phase 5 origin/run/gap fields); `research_query_subquestions`; `research_search_results`; `research_result_occurrences`; `research_seeds` links selected results to crawl jobs. |
| Index | `knowledge_documents` references crawled pages; `knowledge_chunks` stores canonical passages and vector IDs; `knowledge_chunk_sources` stores occurrence/adjacency; `index_jobs` and `index_document_statuses` track work; `knowledge_chunks_fts` is derived lexical index. |
| Agent | `agent_runs` owns budgets/state/counters; `agent_iterations` holds structured trace JSON; `research_gaps`; `evidence_assessments`; `agent_actions` provide lineage and resume checks. Graph checkpoints are in a separate SQLite file. |
| Evidence | `evidence_jobs` owns fingerprint/request/progress; `verified_claims` owns typed claim/status; `claim_origins`; `claim_evidence_relations` stores scores, decisions, and qualifier flags; `claim_citations` stores exact offsets/text; `source_groups` records independent source identity. |
| Graph | `graph_build_jobs` tracks asynchronous work; `graph_snapshots` stores source fingerprint, schema version, hash, validation and metrics; `graph_nodes` and `graph_edges` store snapshot-scoped materialization. |

## 6. Configuration and dependencies

`app/core/config.py` uses `pydantic-settings` with `SPIDERMIND_` environment prefix and optional `.env`. Its validators enforce a consistent chunk profile and loopback-only plain HTTP Ollama URL. Important defaults and knobs:

| Group | Defaults and controls |
| --- | --- |
| Database/network | SQLite `./spidermind.db`; `SpiderMindBot/0.1`; request timeout 10 s, crawl timeout 120 s, domain delay 1 s, redirects 5, retries 2. |
| Crawl/embeddings | `BAAI/bge-m3`; batch 16; candidate context 280 chars; page scoring 1200 chars; depth penalty 0.02; exploration 0.10; optional relevance threshold. |
| Planner/search/seeds | Loopback Ollama on port 11434, `Qwen3:latest`, planner timeout 180 s; search retries 2, search concurrency 2, seed domain cap 1, semantic weight 0.85. Config advertises crawl concurrency 1; orchestration currently serializes seed crawls. |
| Chunk/index | Target 500, max 650, overlap 80, min 80 tokens; local Qdrant path `data/qdrant`, collection `spidermind_chunks`. |
| Retrieval | Dense/lexical top K 50 each, fusion 30, RRF K 60, equal weights; rerank 30 candidates with BGE reranker on CPU, batch 4; final top K 8, max 2 chunks/document, one neighbor each side, 700-token neighbor budget. |
| Agent | Checkpoints `data/langgraph-checkpoints.sqlite`; 5 iterations, 12 new queries, 10 seeds, 40 attempted pages, 900 s; 3 chunks and 2 distinct sources minimum; top K 8; 2 stagnant iterations; bounded assessment packs and query generation. |
| Evidence | DeBERTa base NLI, CPU, batch 2; up to 4 subquestions, 5 claims each, 8 candidates each; semantic duplicate threshold 0.88; adjudication enabled at 0.18 margin; claim retrieval K 40 dense/40 lexical/30 fused/20 rerank. Request limits may override service defaults. |
| Graph | Max neighborhood depth 4, 200 nodes, 500 edges; max 3 shortest paths and 8 path hops; uncited relations included by default; no full chunk text in export. |

`backend/requirements.txt` contains FastAPI, Uvicorn, HTTPX, BeautifulSoup, Trafilatura, Pydantic Settings, SQLAlchemy async, aiosqlite, pytest, pytest-asyncio, and Ruff. Each later requirements file includes the previous phase: intelligent adds Sentence Transformers; research adds `ddgs`; RAG adds `qdrant-client`; agent adds LangGraph and SQLite checkpointer; evidence adds Transformers, Torch, and SentencePiece; graph adds no package. `pyproject.toml` configures pytest asyncio mode/test path and Ruff E/F/I/B/UP rules with 100-character line length. `.gitignore` excludes local environments/caches, SQLite files, Qdrant data, `.env`, and live-run stores. `.vscode/settings.json` only configures editor colors.

## 7. File-by-file guide

This lists every Python source/test/script file and every tracked text artifact, with related files grouped where the responsibility is shared. Empty `__init__.py` files mark packages; `agent`, `rag`, `research`, and `evidence` initializers also contain short package docstrings.

### Application and API

| File | What it does |
| --- | --- |
| `backend/app/main.py` | FastAPI construction, service injection, startup recovery, shutdown. |
| `backend/app/core/config.py` | Environment settings and cross-field validators. |
| `backend/app/core/logging.py` | Structured JSON log formatter and logger setup. |
| `backend/app/api/routes/health.py` | Liveness/readiness. |
| `backend/app/api/routes/crawl.py` | Crawl creation/status/link inspection. |
| `backend/app/api/routes/research.py` | Research creation/status/plan/search/source views. |
| `backend/app/api/routes/rag.py` | Index creation/status/consistency and retrieval. |
| `backend/app/api/routes/agent.py` | Agent start/status/iteration/gap/cancel views. |
| `backend/app/api/routes/evidence.py` | Evidence start/status/claim/contradiction/ledger views. |
| `backend/app/api/routes/graph.py` | Graph build/status, nodes/edges, traversal, analytics, consistency and export endpoints. |
| `backend/app/schemas/crawl.py` | Crawl request validation and response models. |
| `backend/app/schemas/research.py` | Research request and validated planner output. |
| `backend/app/schemas/rag.py` | Retrieval request and filter limits. |

### Crawl, research, and SQL

| File | What it does |
| --- | --- |
| `backend/app/crawler/crawler.py` | Crawl loop, attempt/counter handling, parse/dedup/score/persist transitions. |
| `backend/app/crawler/models.py` | Crawl enums and frontier/fetch/link/parsed-page data objects. |
| `backend/app/crawler/frontier.py` | FIFO and heap priority frontiers with deterministic exploration. |
| `backend/app/crawler/link_scheduler.py` | Link scope, scoring, admissions, and rejection updates. |
| `backend/app/crawler/normalizer.py` | URL normalization, host extraction, private IP checks. |
| `backend/app/crawler/security.py` | DNS/public-target SSRF guard. |
| `backend/app/crawler/fetcher.py` | Bounded async HTTP streaming, redirects, retries, content checks. |
| `backend/app/crawler/rate_limit.py` | Shared per-domain request spacing. |
| `backend/app/crawler/robots.py` | Cached robots.txt decisions by origin/user agent. |
| `backend/app/crawler/parser.py` | HTML title/description/canonical/text/link extraction. |
| `backend/app/crawler/deduplicator.py` | SHA-256 extracted-text deduplication. |
| `backend/app/crawler/embedding.py` | Replaceable embedding interface and lazy local model. |
| `backend/app/crawler/scoring.py` | Candidate/page representations, cosine, query/candidate scoring. |
| `backend/app/research/planner.py` | Ollama JSON planning with validation/repair. |
| `backend/app/research/prompts.py` | Question-only planning prompts. |
| `backend/app/research/search.py` | Search provider interface and `ddgs` adapter. |
| `backend/app/services/crawl_service.py` | Crawl job creation/task lifecycle and shared HTTP/robots/rate-limit state. |
| `backend/app/services/research_service.py` | Query dedup/search/result persistence, seed scoring/selection, crawl budgets. |
| `backend/app/db/database.py` | Async SQLAlchemy engine/session, initialization and ready check. |
| `backend/app/db/migrations.py` | Additive columns, FTS5 virtual table/triggers, schema version 7. |
| `backend/app/db/models.py` | Crawl and research relational models/constraints. |
| `backend/app/db/repositories.py` | Crawl job/page/link persistence and lookup. |

### RAG, agent, and evidence

| File | What it does |
| --- | --- |
| `backend/app/rag/chunking.py` | BGE tokenizer abstraction and token-bounded passage segmentation. |
| `backend/app/rag/models.py` | Document/chunk/source/index job SQL models. |
| `backend/app/rag/indexing.py` | Idempotent incremental index, vector repair, orphan removal, job metrics. |
| `backend/app/rag/vector.py` | Vector interface and local Qdrant adapter. |
| `backend/app/rag/lexical.py` | Safe FTS query and fallback local BM25. |
| `backend/app/rag/fusion.py` | Weighted reciprocal rank fusion. |
| `backend/app/rag/reranker.py` | Reranker interface and local cross encoder. |
| `backend/app/rag/retrieval.py` | Filtered dense/lexical/hybrid search, reranking, neighbor context. |
| `backend/app/rag/consistency.py` | SQL/FTS/Qdrant integrity comparison. |
| `backend/app/agent/enums.py` | Agent/gap/action/stop/priority enums. |
| `backend/app/agent/models.py` | Run/iteration/gap/assessment/action SQL models. |
| `backend/app/agent/schemas.py` | Agent request and typed assessor/query outputs. |
| `backend/app/agent/llm.py` | Bounded untrusted evidence envelope, Ollama structured assessor/query generator. |
| `backend/app/agent/service.py` | Budget manager, LangGraph nodes, action persistence, resume, cancellation. |
| `backend/app/evidence/models.py` | Evidence job/claim/origin/relation/citation/source-group SQL models. |
| `backend/app/evidence/schemas.py` | Claim/relation/status enums and typed requests/LLM responses. |
| `backend/app/evidence/llm.py` | Ollama extraction/equivalence/counterquery/adjudication interfaces/prompts. |
| `backend/app/evidence/nli.py` | Lazy Transformers NLI, model label mapping, batching/counters. |
| `backend/app/evidence/logic.py` | Claim normalization/merging/scope checks, source grouping, citation/status rules. |
| `backend/app/evidence/service.py` | Fingerprint/recovery, extraction, dedup, retrieval, verification, citations, status. |
| `backend/app/graph/types.py` | Graph schema version and explicit node/edge enums. |
| `backend/app/graph/models.py` | Graph build job/snapshot/node/edge SQL models. |
| `backend/app/graph/builder.py` | Scoped domain loading, fingerprints, stable IDs, deterministic materialization. |
| `backend/app/graph/store.py` | Snapshot-scoped SQLite graph store. |
| `backend/app/graph/projection.py` | Directed multigraph adjacency, bounded traversal and components. |
| `backend/app/graph/validation.py` | Domain-to-graph consistency and provenance integrity checks. |
| `backend/app/graph/analytics.py` | Structural claim/source counts and degree distributions. |
| `backend/app/graph/query.py` | Node/edge filtering, claim/contradiction views and summaries. |
| `backend/app/graph/export.py` | Stable JSON graph export. |
| `backend/app/graph/service.py` | Asynchronous build/recovery/idempotency lifecycle. |

### Evaluation code and scripts

| File | What it does |
| --- | --- |
| `backend/app/evaluation/compare.py` | Crawl yield/efficiency metrics and CLI comparison. |
| `backend/app/evaluation/demo.py` | Controlled Phase 2 fixture HTTP/model demonstration. |
| `backend/app/evaluation/phase3.py` | Plan, seed, and crawl fixture metrics. |
| `backend/app/evaluation/phase3_smoke.py` | Phase 3 bounded public-data smoke. |
| `backend/app/evaluation/phase4.py` | Ranking metric calculations. |
| `backend/app/evaluation/phase4_benchmark.py` | Four chunk-profile controlled retrieval benchmark. |
| `backend/app/evaluation/phase4_live_smoke.py` | Existing Phase 3 data index/retrieval smoke. |
| `backend/app/evaluation/phase5.py` | Agent retrieval/source/sufficiency metrics. |
| `backend/app/evaluation/phase6.py` | Claim/relation/citation/status metric helpers. |
| `backend/scripts/evaluate_phase5.py` | Controlled full agent scenario evaluation and JSON result. |
| `backend/scripts/live_phase5_smoke.py` | Bounded live Phase 5 run on copied DB. |
| `backend/scripts/evaluate_phase6.py` | Controlled Phase 6 pipeline evaluation. |
| `backend/scripts/evaluate_phase6_nli.py` | Six-pair real local NLI pilot. |
| `backend/scripts/evaluate_phase6_hybrid.py` | Real NLI plus Qwen3 adjudication pilot. |
| `backend/scripts/live_phase6_smoke.py` | Bounded live Phase 6 connectivity run. |
| `backend/scripts/show_agent_graph.py` | Prints LangGraph Mermaid graph. |
| `backend/scripts/evaluate_phase7.py` | Controlled Phase 7 fixture metrics and JSON result. |
| `backend/scripts/live_phase7_smoke.py` | Offline graph build on a copied successful Phase 6 database. |

### Tests and fixtures

| File | Coverage |
| --- | --- |
| `backend/tests/test_components.py` | URL normalization, frontier, dedup, parser, private targets, fetch/redirect limits. |
| `backend/tests/test_crawl_api.py` | Scope/depth/robots/duplicates/persistence, max pages, timeout, API behavior. |
| `backend/tests/test_phase2.py` | Mode/scoring/provider/priority/exploration, threshold, migration, safety. |
| `backend/tests/test_phase3.py` | Query/result dedup, global budget, failure isolation, shared seeds, lineage. |
| `backend/tests/test_research_adapters.py` | Planner repair/validation, DDGS mapping, unsafe result rejection. |
| `backend/tests/test_phase4.py` | Chunk/fusion/index/retrieval idempotency and replacement. |
| `backend/tests/test_phase5.py` | Graph scenarios, bounded prompts, resume, budgets, cancellation, acquisition, safety. |
| `backend/tests/test_evidence_engine.py` | Evidence API, citations, source groups, fingerprint idempotency. |
| `backend/tests/test_evidence_llm.py` | Atomic claims, qualifiers, ambiguity routing, prompt envelope. |
| `backend/tests/test_evidence_rules.py` | Merge/qualifier guards, NLI label mapping, citation spans, status/group rules. |
| `backend/tests/graph_fixture.py` | Deterministic multi-status, mirrored-source, two-job graph fixture. |
| `backend/tests/test_graph_builder.py` | Stable IDs, graph materialization, migration, idempotency, changed evidence, validation. |
| `backend/tests/test_graph_queries.py` | Filters, paths, neighborhoods, claim views, analytics and JSON round trip. |
| `backend/tests/test_graph_api.py` | HTTP graph contract, bounds, consistency and research-job isolation. |
| `backend/tests/test_graph_validation.py` | Changed citation spans and foreign graph node detection. |
| `backend/tests/fixtures/phase3_benchmarks.json` | Labeled facets, seeds, and crawl-page targets. |
| `backend/tests/fixtures/phase4_benchmark.json` | Eight controlled retrieval queries and relevant documents. |
| `backend/tests/fixtures/research_site/index.html` | Local fixture link hub. |
| `backend/tests/fixtures/research_site/ai/{embeddings,hallucination,rag-evaluation}.html` | Three labeled relevant AI pages. |
| `backend/tests/fixtures/research_site/company/{about,careers,pricing}.html` | Company distractor pages. |
| `backend/tests/fixtures/research_site/unrelated/{cooking,gardening}.html` | Unrelated distractor pages. |
| `backend/tests/fixtures/research_site/labels.json` | Relevant fixture URL list. |

### Phase 8A frontend

| File | What it does |
| --- | --- |
| `frontend/package.json`, `package-lock.json` | React/TypeScript/Vite/React Flow dependencies and reproducible scripts. |
| `frontend/vite.config.ts`, `tsconfig*.json`, `eslint.config.js` | Development proxy, TypeScript, Vitest, and lint configuration. |
| `frontend/index.html`, `src/main.tsx` | Browser entry point and stylesheet loading. |
| `frontend/src/app/App.tsx` | UI composition, graph selection, filters, overlays, and inspector state. |
| `frontend/src/api/{client,graphApi}.ts` | Timed HTTP client and Phase 7 endpoint functions. |
| `frontend/src/types/graph.ts` | Explicit node/edge/status DTO unions matching Phase 7. |
| `frontend/src/hooks/useSpiderGraph.ts` | Build/load/poll, bounded expansion, detail/claim/path/contradiction state. |
| `frontend/src/features/graph/{visuals,graphLayout,graphAdapter}.ts` | Visual semantics, deterministic coordinates, filters, and React Flow conversion. |
| `frontend/src/components/layout/{TopHeader,Sidebar}.tsx` | Search, navigation, status, and graph filters. |
| `frontend/src/components/graph/{GraphNodeCard,SemanticEdge,SpiderHub,GraphToolbar,GraphLegend,GraphWorkspace}.tsx` | Custom graph canvas, spider visual, controls, and legend. |
| `frontend/src/components/inspector/InspectorDrawer.tsx` | Type-specific inspection and claim evidence/citation views. |
| `frontend/src/components/research/GraphLoader.tsx` | Snapshot or evidence-job startup form. |
| `frontend/src/utils/safety.ts` | Safe HTTP(S) source links and short IDs. |
| `frontend/src/styles/global.css` | Dark responsive design, web motif, reduced-motion behavior. |
| `frontend/src/tests/{fixture,setup,graphLogic,safetyAndApi,InspectorDrawer,Navigation,App.integration,liveApi}.*` | Controlled DTO fixtures, frontend behavior tests, and an optional real Phase 7 API integration test. |
| `frontend/src/assets/README.md` | Placement for the two required PNG assets, which were absent at implementation time. |
| `frontend/README.md`, `.env.example` | Setup, API origin, interaction, and quality-gate documentation. |
| `backend/scripts/prepare_phase8a_ui_smoke.py` | Copies completed Phase 6 data and builds a real local Phase 7 snapshot for UI development. |

### Documentation and recorded runs

`README.md` is the user-facing setup/API/phase guide. `docs/architecture.md` expands the implementation, provenance, security, and limitations by phase. The JSON files below are recorded outputs, not live guarantees:

| File | Recorded purpose |
| --- | --- |
| `docs/phase2-demo-results.json` | Same four-page fixture: FIFO 1/4 relevant vs intelligent 3/4. |
| `docs/phase3-controlled-results.json` | Deterministic fake-provider plan/seed/crawl metrics. |
| `docs/phase3-live-smoke-results.json` | One public-search research smoke. |
| `docs/phase4-benchmark-results.json` | Dense/BM25/hybrid/rerank metrics for four chunk profiles and eight queries. |
| `docs/phase4-live-smoke-results.json` | Index, consistency, and retrieval on prior Phase 3 data. |
| `docs/phase5-agent-results.json` | Controlled agent scenarios; relevant source recall@8 rises 0.5 → 1.0 in its fixture. |
| `docs/phase5-live-smoke-results.json` | One live agent run; stopped at max iterations with no new source. |
| `docs/phase6-evidence-results.json` | Fake-component controlled pipeline, citation/status cases, failure counters. |
| `docs/phase6-nli-base-results.json` | Six-pair real base NLI pilot; macro F1 0.7222 before scope guard. |
| `docs/phase6-hybrid-pilot-results.json` | Six-pair scope-guarded NLI plus Qwen3 pilot; both macro F1 0.8667, two LLM calls. |
| `docs/phase6-live-smoke-attempt1.json` | First bounded live attempt; timeout. |
| `docs/phase6-live-smoke-attempt2.json` | Second bounded live attempt and claim/ledger snapshots. |
| `docs/phase6-live-smoke-results.json` | Final unlabeled connectivity run, completed with two extracted claims. |
| `docs/phase7-graph-results.json` | Controlled graph counts, coverage, query accuracy, determinism, export and timings. |
| `docs/phase7-live-smoke-results.json` | Phase 6-to-7 copied-live-data graph and actual provenance paths. |
| `docs/phase8a-ui-smoke-results.json` | Real copied graph API/client smoke, quality gates, and explicit visual-verification blockers. |

The controlled Phase 5/6 fixtures use fake model components, so perfect fixture scores do not estimate real-world model accuracy. The real Phase 6 NLI pilot has only six labeled pairs. Phase 4's 180- and 500-token profiles each create eight chunks for its short documents, so that fixture cannot distinguish them. Live smokes check connectivity and execution, not labeled accuracy.

### Local binary/runtime artifacts

The root contains `spidermind.db`, `spidermind-demo.db`, `spidermind-phase3-smoke.db`, and `spidermind-phase3-smoke-final.db`. `backend/` also contains `spidermind.db` and an empty phase3 final DB. `data/` contains Phase 5/6 copied live-run SQLite DBs, checkpoint SQLite files, local Qdrant SQLite collections, their `meta.json` files, and `.lock` files; `backend/data/` contains empty checkpoint/local-Qdrant metadata. They are generated snapshots, not source modules. SQLite table inventories show the expected progression from crawl-only through research, agent, and evidence schemas. `.git`, `.venv`, pytest/Ruff caches, and Python bytecode are tool/dependency artifacts outside the application source inventory.

## 8. Security, reliability, and practical limits

- The crawler checks DNS/public addresses and redirect scope before fetching, but HTTPX resolves again on connection. DNS rebinding remains possible; production use with untrusted callers needs connection-level pinning and outbound network controls.
- There is no authentication or authorization on the API. A public deployment would need access control, quotas, and durable worker isolation before exposing crawl/model operations.
- Background crawl/research/index/evidence work is process local. Crawl/research jobs are failed on restart; index jobs become partial/retryable; evidence jobs are rescheduled; agent runs resume from checkpoints. Multiple Uvicorn workers do not share in-memory task, dedup, lock, or budget coordination.
- SQLite, FTS, Qdrant, and checkpoint writes cannot commit atomically together. The consistency endpoint and idempotent reindexing address some drift. A durable queue and stronger transactional orchestration would be needed for a multi-worker deployment.
- The crawler reads static HTML, not JavaScript-rendered content, sitemaps, authenticated pages, or robots crawl-delay. Canonical URLs are metadata, not crawl identity. Domain allowlisting is exact-host based.
- Prompt injection is bounded with question-only planning, local schema validation, untrusted passage envelopes, escaped delimiters, and non-tool LLM calls. Claim grounding, semantic equivalence, qualifier parsing, sentence NLI, and source-grouping are still fallible. NLI scores and evidence confidence tiers must not be interpreted as probabilities of truth.

## 9. Verification commands

From the repository root, with the relevant dependencies installed:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests
.\.venv\Scripts\python.exe -m ruff check backend
cd frontend
npm run lint
npm run test
npm run build
```

For targeted development, `pytest backend/tests/test_phase2.py`, `test_phase3.py`, `test_phase4.py`, `test_phase5.py`, or the three evidence test files isolate a phase. Controlled evaluation commands are documented in `README.md`; they may load local models and create SQLite/Qdrant artifacts. Run smoke scripts only when a bounded live search/model run is intended.

## 10. How this context was assembled

The original Phase 1–6 inventory covered 165 non-cache files (93 Python files) before this document and the Phase 7 implementation were added. The Phase 7 and 8A source, tests, scripts, requirements, result JSON, and documentation are included in the guide above. Python files parsed successfully as Python AST; JSON files parsed successfully; text files decoded as UTF-8; SQLite files were opened read-only for table inventories. The application, route, schema, service, model, test, fixture, script, and evaluation behavior was cross-checked against the current source and recorded artifacts. The Phase 8A API and frontend tests passed, but visual review remains pending because the referenced PNGs and an in-app browser were unavailable. This document summarizes each file's role and important contracts; the source remains authoritative for every branch and field.

## Appendix A. Complete `SPIDERMIND_` settings inventory

Each name below is an environment variable. Values are the code defaults; request fields can override some per-job choices. `None` means unset. See `backend/app/core/config.py` for validation bounds.

| Variable | Default |
| --- | --- |
| `SPIDERMIND_DATABASE_URL` | `sqlite+aiosqlite:///./spidermind.db` |
| `SPIDERMIND_USER_AGENT` | `SpiderMindBot/0.1 (+local research crawler)` |
| `SPIDERMIND_REQUEST_TIMEOUT_SECONDS` | `10.0` |
| `SPIDERMIND_CRAWL_TIMEOUT_SECONDS` | `120.0` |
| `SPIDERMIND_DOMAIN_DELAY_SECONDS` | `1.0` |
| `SPIDERMIND_MAX_REDIRECTS` | `5` |
| `SPIDERMIND_MAX_RETRIES` | `2` |
| `SPIDERMIND_EMBEDDING_MODEL_NAME` | `BAAI/bge-m3` |
| `SPIDERMIND_EMBEDDING_BATCH_SIZE` | `16` |
| `SPIDERMIND_MAX_CANDIDATE_CONTEXT_CHARS` | `280` |
| `SPIDERMIND_MAX_PAGE_SCORING_CHARS` | `1200` |
| `SPIDERMIND_DEPTH_PENALTY` | `0.02` |
| `SPIDERMIND_EXPLORATION_RATE` | `0.1` |
| `SPIDERMIND_DEFAULT_MIN_RELEVANCE_SCORE` | `None` |
| `SPIDERMIND_OLLAMA_URL` | `http://127.0.0.1:11434` |
| `SPIDERMIND_OLLAMA_MODEL` | `Qwen3:latest` |
| `SPIDERMIND_PLANNER_TIMEOUT_SECONDS` | `180` |
| `SPIDERMIND_SEARCH_RETRIES` | `2` |
| `SPIDERMIND_MAX_CONCURRENT_SEARCHES` | `2` |
| `SPIDERMIND_MAX_CONCURRENT_SUBQUESTION_CRAWLS` | `1` |
| `SPIDERMIND_MAX_SEEDS_PER_DOMAIN_PER_SUBQUESTION` | `1` |
| `SPIDERMIND_SEED_SEMANTIC_WEIGHT` | `0.85` |
| `SPIDERMIND_CHUNK_TARGET_TOKENS` | `500` |
| `SPIDERMIND_CHUNK_MAX_TOKENS` | `650` |
| `SPIDERMIND_CHUNK_OVERLAP_TOKENS` | `80` |
| `SPIDERMIND_CHUNK_MIN_TOKENS` | `80` |
| `SPIDERMIND_QDRANT_PATH` | `data/qdrant` |
| `SPIDERMIND_QDRANT_COLLECTION` | `spidermind_chunks` |
| `SPIDERMIND_AGENT_CHECKPOINT_PATH` | `data/langgraph-checkpoints.sqlite` |
| `SPIDERMIND_AGENT_MAX_ITERATIONS` | `5` |
| `SPIDERMIND_AGENT_MAX_QUERIES` | `12` |
| `SPIDERMIND_AGENT_MAX_SEEDS` | `10` |
| `SPIDERMIND_AGENT_MAX_PAGES` | `40` |
| `SPIDERMIND_AGENT_MAX_RUNTIME_SECONDS` | `900` |
| `SPIDERMIND_AGENT_MIN_EVIDENCE_CHUNKS` | `3` |
| `SPIDERMIND_AGENT_MIN_UNIQUE_SOURCES` | `2` |
| `SPIDERMIND_AGENT_MAX_ASSESSMENT_CHUNKS` | `6` |
| `SPIDERMIND_AGENT_MAX_ASSESSMENT_CHARS_PER_CHUNK` | `1200` |
| `SPIDERMIND_AGENT_MAX_TOTAL_ASSESSMENT_CHARS` | `7000` |
| `SPIDERMIND_AGENT_MAX_QUERIES_PER_GAP` | `2` |
| `SPIDERMIND_AGENT_MAX_STAGNANT_ITERATIONS` | `2` |
| `SPIDERMIND_AGENT_RERANK_ENABLED` | `True` |
| `SPIDERMIND_AGENT_RETRIEVAL_TOP_K` | `8` |
| `SPIDERMIND_AGENT_MODEL` | `None` |
| `SPIDERMIND_AGENT_TEMPERATURE` | `0` |
| `SPIDERMIND_AGENT_SEARCH_RESULTS_PER_QUERY` | `8` |
| `SPIDERMIND_AGENT_MAX_PAGES_PER_SEED` | `4` |
| `SPIDERMIND_AGENT_CRAWL_MAX_DEPTH` | `2` |
| `SPIDERMIND_EVIDENCE_NLI_MODEL` | `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` |
| `SPIDERMIND_EVIDENCE_NLI_FALLBACK_MODEL` | `None` |
| `SPIDERMIND_EVIDENCE_NLI_BATCH_SIZE` | `2` |
| `SPIDERMIND_EVIDENCE_NLI_DEVICE` | `cpu` |
| `SPIDERMIND_EVIDENCE_CLAIM_EXTRACTION_BATCH_SIZE` | `1` |
| `SPIDERMIND_EVIDENCE_MAX_CANDIDATES_PER_CLAIM` | `8` |
| `SPIDERMIND_EVIDENCE_MAX_CLAIMS_PER_SUBQUESTION` | `5` |
| `SPIDERMIND_EVIDENCE_MAX_SUBQUESTIONS` | `4` |
| `SPIDERMIND_EVIDENCE_ADJUDICATION_ENABLED` | `True` |
| `SPIDERMIND_EVIDENCE_ADJUDICATION_MARGIN_THRESHOLD` | `0.18` |
| `SPIDERMIND_EVIDENCE_ADJUDICATE_ALL` | `False` |
| `SPIDERMIND_EVIDENCE_SEMANTIC_DUPLICATE_THRESHOLD` | `0.88` |
| `SPIDERMIND_EVIDENCE_CLAIM_DENSE_TOP_K` | `40` |
| `SPIDERMIND_EVIDENCE_CLAIM_LEXICAL_TOP_K` | `40` |
| `SPIDERMIND_EVIDENCE_CLAIM_FUSION_TOP_K` | `30` |
| `SPIDERMIND_EVIDENCE_CLAIM_RERANK_TOP_K` | `20` |
| `SPIDERMIND_DENSE_TOP_K` | `50` |
| `SPIDERMIND_LEXICAL_TOP_K` | `50` |
| `SPIDERMIND_FUSION_TOP_K` | `30` |
| `SPIDERMIND_RRF_K` | `60` |
| `SPIDERMIND_RRF_DENSE_WEIGHT` | `1.0` |
| `SPIDERMIND_RRF_LEXICAL_WEIGHT` | `1.0` |
| `SPIDERMIND_RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` |
| `SPIDERMIND_RERANKER_DEVICE` | `cpu` |
| `SPIDERMIND_RERANKER_BATCH_SIZE` | `4` |
| `SPIDERMIND_RERANKER_CANDIDATE_COUNT` | `30` |
| `SPIDERMIND_RERANK_ENABLED` | `True` |
| `SPIDERMIND_FINAL_TOP_K` | `8` |
| `SPIDERMIND_MAX_FINAL_CHUNKS_PER_DOCUMENT` | `2` |
| `SPIDERMIND_NEIGHBOR_EXPANSION_ENABLED` | `True` |
| `SPIDERMIND_NEIGHBOR_WINDOW` | `1` |
| `SPIDERMIND_MAX_NEIGHBOR_CONTEXT_TOKENS` | `700` |
| `SPIDERMIND_GRAPH_MAX_TRAVERSAL_DEPTH` | `4` |
| `SPIDERMIND_GRAPH_MAX_SUBGRAPH_NODES` | `200` |
| `SPIDERMIND_GRAPH_MAX_SUBGRAPH_EDGES` | `500` |
| `SPIDERMIND_GRAPH_MAX_PATHS` | `3` |
| `SPIDERMIND_GRAPH_MAX_PATH_HOPS` | `8` |
| `SPIDERMIND_GRAPH_INCLUDE_UNCITED_RELATIONS` | `True` |
| `SPIDERMIND_GRAPH_EXPORT_INCLUDE_CHUNK_TEXT` | `False` |

## Appendix B. Generated SQLite and Qdrant artifact paths

These are local snapshots present at inventory time. They are not required to understand or build the source, and they may change between runs. Empty SQLite files are placeholders.

- `backend/data/langgraph-checkpoints.sqlite`
- `backend/data/qdrant/.lock`
- `backend/data/qdrant/meta.json`
- `backend/spidermind-phase3-smoke-final.db`
- `backend/spidermind.db`
- `data/phase5-live-20261009-135809.db`
- `data/phase5-live-checkpoints-20261009-135809.sqlite`
- `data/phase5-live-qdrant-20261009-135809/.lock`
- `data/phase5-live-qdrant-20261009-135809/collection/spidermind_chunks/storage.sqlite`
- `data/phase5-live-qdrant-20261009-135809/meta.json`
- `data/phase6-live-20261010-094822.db`
- `data/phase6-live-20261010-095958.db`
- `data/phase6-live-20261010-101125.db`
- `data/phase6-live-20261010-102304.db`
- `data/phase6-live-checkpoints-20261010-094822.sqlite`
- `data/phase6-live-checkpoints-20261010-095958.sqlite`
- `data/phase6-live-checkpoints-20261010-101125.sqlite`
- `data/phase6-live-checkpoints-20261010-102304.sqlite`
- `data/phase6-live-qdrant-20261010-094822/.lock`
- `data/phase6-live-qdrant-20261010-094822/collection/spidermind_chunks/storage.sqlite`
- `data/phase6-live-qdrant-20261010-094822/meta.json`
- `data/phase6-live-qdrant-20261010-095958/.lock`
- `data/phase6-live-qdrant-20261010-095958/collection/spidermind_chunks/storage.sqlite`
- `data/phase6-live-qdrant-20261010-095958/meta.json`
- `data/phase6-live-qdrant-20261010-101125/.lock`
- `data/phase6-live-qdrant-20261010-101125/collection/spidermind_chunks/storage.sqlite`
- `data/phase6-live-qdrant-20261010-101125/meta.json`
- `data/phase6-live-qdrant-20261010-102304/.lock`
- `data/phase6-live-qdrant-20261010-102304/collection/spidermind_chunks/storage.sqlite`
- `data/phase6-live-qdrant-20261010-102304/meta.json`
- `data/qdrant/.lock`
- `data/qdrant/meta.json`
- `spidermind-demo.db`
- `spidermind-phase3-smoke-final.db`
- `spidermind-phase3-smoke.db`
- `spidermind.db`
