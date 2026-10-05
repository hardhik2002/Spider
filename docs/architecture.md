# SpiderMind crawler architecture: Phases 1 and 2

## Lifecycle and boundaries

1. `POST /api/v1/crawl` validates the Pydantic request, normalizes and checks the start URL, persists a `PENDING` job, and schedules an in-process task.
2. The task marks the job `RUNNING` and inserts the start URL into the selected frontier. The seed is always crawled first and has no predicted link score. Frontier items hold URL, normalized URL, depth, parent, discovery time/order, link context, predicted relevance, and priority.
3. For each item, the crawler writes a `QUEUED` page, checks cached robots policy, moves to `FETCHING`, then asks the fetcher for a bounded HTML response. The fetcher checks the target and each redirect, applies shared per-domain spacing, validates status and content type, and streams only up to `max_content_size`.
4. The parser extracts metadata, readable text, and links. It captures links before removing navigation and footer elements from text extraction. Trafilatura supplies main text, with a BeautifulSoup fallback.
5. The crawler hashes normalized text. A repeated hash yields `SKIPPED` and `duplicate_of_page_id`; the repeated text is omitted. Other parsed pages become `COMPLETED`. All extracted HTTP links are stored in `discovered_links`, including external links. The link scheduler records eligibility and, in intelligent mode, scores eligible candidates as one batch per page before admitting them to the frontier.
6. Per-page fetch/parse errors become `FAILED` without ending the job. Robots denials, out-of-scope links, depth exclusions, duplicate content, and leftover frontier items at `max_pages` count as skipped. The job ends `COMPLETED` when traversal finishes, or `FAILED` on a job-wide timeout or unexpected orchestration error.

The API status counters are committed during the crawl. A job may be `COMPLETED` with failed pages; the job status describes orchestration, while page status describes each URL. JSON log records include `job_id` and event names without page bodies.

## Why components are separate

- **Normalizer:** One identity for equivalent HTTP URLs, relative links, fragments, host casing, default ports, and redundant trailing slashes. Unsupported schemes are rejected before frontier admission.
- **Frontiers:** `Frontier` preserves FIFO/BFS. `PriorityFrontier` orders scored items and implements deterministic exploration. Neither has HTTP, database, parsing, or embedding knowledge.
- **Target validator:** Rejects local, private, link-local, and other non-global IP literals and DNS answers. Every HTTP redirect is checked. The client does not inherit proxy settings.
- **Fetcher:** Owns async HTTP behavior, transient retries (429 and common 5xx plus network errors), redirect count, size and type validation, and response metrics. Permanent 4xx errors are not retried.
- **Robots manager:** Parses rules for the configured user-agent and caches them per origin. Missing policy (404/410) allows access; other retrieval failures deny access. Redirect targets are checked against scope and robots before fetching.
- **Rate limiter:** Uses a per-domain lock and next-allowed timestamp shared by all jobs in one process. This permits future domain-specific intervals, robots crawl-delay, and multiple domains in flight without changing the fetcher API.
- **Parser:** Builds structured metadata and links, including a bounded nearby paragraph/list/parent context. Internal/external classification compares exact hostnames with the start host.
- **Link scheduler:** Applies depth, domain, duplicate, private-address, and optional relevance filters; batches candidates through the scorer; records each decision and admits selected items to the frontier.
- **Embedding provider and scorer:** The provider owns one reusable local Sentence Transformers model; the scorer builds bounded candidate/page representations, caches a query vector per job, batches candidate embeddings, computes cosine similarity, and applies the depth penalty.
- **Deduplicator:** SHA-256 of whitespace-normalized, case-folded extracted text prevents duplicate text records. The original page ID remains queryable.
- **Repository and SQLAlchemy models:** Jobs, pages, and link edges remain distinct. `source_page_id`, normalized target, and indexes provide a straightforward future knowledge-graph import path.

## Limits and recovery

`max_depth` controls which discovered links enter the frontier. `max_pages` limits popped items, including skipped and failed attempts. `timeout` is an overall job deadline. `max_content_size` is checked from `Content-Length` where available and while streaming. A redirect cannot leave the configured crawl scope. A startup recovery pass marks abandoned `PENDING` and `RUNNING` jobs as `FAILED`. The API uses one process; running multiple Uvicorn workers would not share task state or rate-limit clocks.

The database uses async SQLAlchemy with SQLite/aiosqlite. SQLite I/O is mediated through a background thread; this is appropriate for local development but is not a high-throughput distributed queue. Phase 2 includes an idempotent, additive SQLite migration for existing Phase 1 files. A full migration tool is needed for future destructive or relational schema changes. A future worker queue should replace the in-process task set and add resume/checkpoint semantics.

## FIFO and intelligent flows

FIFO remains the request default, including when no `crawl_mode` field is sent. Its scheduler checks scope and depth and admits eligible links in discovery order. An optional `research_query` on a FIFO job enables page-level relevance measurement without changing the ordering.

Intelligent mode requires a query. The scorer embeds it once per job; the provider loads BGE-M3 once per API process and reuses it. Each parsed page supplies its eligible links as one batch. Exact candidate representations are cached per job to avoid repeat embedding work. Out-of-scope and depth-excluded links are stored with rejection reasons but not embedded.

```text
 Research query ──> one query embedding ────────────────────────────┐
                                                                    │
 Page ──> parser ──> links + bounded nearby text                   │
                       │                                            │
                       v                                            │
             candidate representations ──> batch embeddings ──> cosine
                                                             │
                                                             v
                                                    depth-aware priority
                                                             │
                                                             v
                                                   priority frontier
                                                             │
                                                             v
                                                          crawler
```

Candidate text contains target URL (up to 500 characters), anchor (200), source title (200), and nearby text (default 280). Page-level outcome scoring embeds title, description, and at most 1,200 content characters after fetching. It is recorded for evaluation and does not alter decisions already made.

For nonzero vectors, `relevance_score = dot(query, candidate) / (norm(query) × norm(candidate))`, bounded to `[-1, 1]`. This is a similarity, not a probability. `priority_score = relevance_score - depth × depth_penalty`; the default penalty is `0.02`. The optional threshold compares **raw relevance** before depth penalty. A score below it receives `LOW_RELEVANCE` and remains visible through `/links`.

The priority frontier pops higher priority first, then shallower depth, then earlier discovery order. With the default exploration rate `0.10`, every tenth pop takes the lowest-priority remaining eligible item if more than one exists. This is deterministic, not random. The threshold remains a hard cutoff; exploration cannot revive a rejected link. The seed bypasses score and threshold. External links remain persisted but enter neither frontier unless external domains are enabled and all other scope rules pass.

The job records mode, query, model, threshold, penalty, exploration rate, scoring counts/times, and score aggregates. Each discovered link records title/context, score, priority, penalty, scoring status, selection, and rejection reason. Each crawled page records predicted link relevance and actual post-fetch page relevance. This is deterministic explainability metadata; no LLM produces explanations.

The evaluation utility compares completed jobs with the same query, seed, scope, depth, and page budget. `relevant_page_yield = relevant completed pages / completed pages`. When fixture labels are supplied, relevance comes from those independent labels; otherwise it uses a declared page-score threshold. `mean_page_relevance = sum(actual page cosine scores) / completed pages`. `high_relevance_hit_rate = pages with actual score ≥ high_threshold / completed pages`. `crawl_efficiency = relevant completed pages / requested max_pages`. Zero denominators yield zero. A controlled fixture and measured output are in `backend/tests/fixtures/research_site` and `docs/phase2-demo-results.json`.

## Phase 3 boundary

Search or research planning can submit multiple seeds and query objectives to the existing API or orchestration layer. The frontier and scorer need not know where the seed came from. Answer generation, retrieval, vector databases, and knowledge graph processing remain outside this crawler.

## Security and operational limits

The validator resolves public hosts before requests, but HTTPX resolves again when connecting; DNS rebinding between those steps is still possible. A production deployment with untrusted users should pin resolved addresses at connection time and enforce outbound firewall rules. Subdomains are distinct hosts. The crawler does not process JavaScript-rendered content, canonical URLs as crawl identity, sitemaps, authentication, or robots crawl-delay directives. Public-site policies and terms remain the operator's responsibility.
