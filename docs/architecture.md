# Phase 1 architecture

## Lifecycle and boundaries

1. `POST /api/v1/crawl` validates the Pydantic request, normalizes and checks the start URL, persists a `PENDING` job, and schedules an in-process task.
2. The task marks the job `RUNNING` and inserts the start URL into the frontier. The frontier holds immutable items with URL, normalized URL, depth, parent, discovery time, and a reserved priority value.
3. For each item, the crawler writes a `QUEUED` page, checks cached robots policy, moves to `FETCHING`, then asks the fetcher for a bounded HTML response. The fetcher checks the target and each redirect, applies shared per-domain spacing, validates status and content type, and streams only up to `max_content_size`.
4. The parser extracts metadata, readable text, and links. It captures links before removing navigation and footer elements from text extraction. Trafilatura supplies main text, with a BeautifulSoup fallback.
5. The crawler hashes normalized text. A repeated hash yields `SKIPPED` and `duplicate_of_page_id`; the repeated text is omitted. Other parsed pages become `COMPLETED`. All extracted HTTP links are stored in `discovered_links`, including external links, then newly seen in-scope links within depth are added to the frontier.
6. Per-page fetch/parse errors become `FAILED` without ending the job. Robots denials, out-of-scope links, depth exclusions, duplicate content, and leftover frontier items at `max_pages` count as skipped. The job ends `COMPLETED` when traversal finishes, or `FAILED` on a job-wide timeout or unexpected orchestration error.

The API status counters are committed during the crawl. A job may be `COMPLETED` with failed pages; the job status describes orchestration, while page status describes each URL. JSON log records include `job_id` and event names without page bodies.

## Why components are separate

- **Normalizer:** One identity for equivalent HTTP URLs, relative links, fragments, host casing, default ports, and redundant trailing slashes. Unsupported schemes are rejected before frontier admission.
- **Frontier:** Encapsulates ordering and URL membership. Today it is FIFO/BFS; it has no HTTP, database, or parsing knowledge.
- **Target validator:** Rejects local, private, link-local, and other non-global IP literals and DNS answers. Every HTTP redirect is checked. The client does not inherit proxy settings.
- **Fetcher:** Owns async HTTP behavior, transient retries (429 and common 5xx plus network errors), redirect count, size and type validation, and response metrics. Permanent 4xx errors are not retried.
- **Robots manager:** Parses rules for the configured user-agent and caches them per origin. Missing policy (404/410) allows access; other retrieval failures deny access. Redirect targets are checked against scope and robots before fetching.
- **Rate limiter:** Uses a per-domain lock and next-allowed timestamp shared by all jobs in one process. This permits future domain-specific intervals, robots crawl-delay, and multiple domains in flight without changing the fetcher API.
- **Parser:** Builds structured metadata and links independent of crawl scheduling. Internal/external classification compares exact hostnames with the start host.
- **Deduplicator:** SHA-256 of whitespace-normalized, case-folded extracted text prevents duplicate text records. The original page ID remains queryable.
- **Repository and SQLAlchemy models:** Jobs, pages, and link edges remain distinct. `source_page_id`, normalized target, and indexes provide a straightforward future knowledge-graph import path.

## Limits and recovery

`max_depth` controls which discovered links enter the frontier. `max_pages` limits popped items, including skipped and failed attempts. `timeout` is an overall job deadline. `max_content_size` is checked from `Content-Length` where available and while streaming. A redirect cannot leave the configured crawl scope. A startup recovery pass marks abandoned `PENDING` and `RUNNING` jobs as `FAILED`. The API uses one process; running multiple Uvicorn workers would not share task state or rate-limit clocks.

The database uses async SQLAlchemy with SQLite/aiosqlite. SQLite I/O is mediated through a background thread; this is appropriate for local Phase 1 but is not a high-throughput distributed queue. Schema creation uses `create_all` only; a migration tool is needed before evolving persisted data. A future worker queue should replace the in-process task set and add resume/checkpoint semantics.

## Phase 2 extension point

The crawler calls only `frontier.push(item)`, `frontier.pop()`, and the frontier's length/truth value. Replace the deque with a heap keyed by `FrontierItem.priority`, or inject a scoring function when links are discovered. The core loop, robots/fetcher/parser interfaces, and database records can remain intact. If a score becomes available after insertion, add a frontier reprioritization method without moving queue logic into the crawler. `DiscoveredLink` already records out-of-scope citations for later graph traversal.

## Security and operational limits

The validator resolves public hosts before requests, but HTTPX resolves again when connecting; DNS rebinding between those steps is still possible. A production deployment with untrusted users should pin resolved addresses at connection time and enforce outbound firewall rules. Subdomains are distinct hosts. The crawler does not process JavaScript-rendered content, canonical URLs as crawl identity, sitemaps, authentication, or robots crawl-delay directives. Public-site policies and terms remain the operator's responsibility.
