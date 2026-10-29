# SpiderMind — research, crawling, and evidence retrieval

SpiderMind is an incremental AI research project. Phase 1 crawls, Phase 2 focuses crawling, Phase 3 plans and searches, Phase 4 indexes and retrieves, Phase 5 iterates to fill evidence gaps, Phase 6 maps atomic claims to supporting and contradicting passages, and Phase 7 materializes a deterministic knowledge/citation graph from those verified records. It does not generate final research answers or provide a frontend.

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

## Phase 3 research planning and search

`POST /api/v1/research` returns a job ID immediately. A local Ollama planner (default `Qwen3:latest`) produces a Pydantic validated JSON plan from the user's question only. One invalid response gets one repair attempt. Queries are deduplicated across subquestions, searched through a replaceable provider (default `ddgs`), and associated with every originating subquestion. Search URLs pass the crawler's normalizer and public-target validator before entering the result pool. The shared local BGE-M3 provider scores bounded title/snippet/URL representations against each subquestion. Selected seeds start Phase 2 intelligent crawls with that subquestion as `research_query`.

Selection uses `seed_score = semantic_weight × ((cosine + 1) / 2) + (1 - semantic_weight) × (1 / log2(rank + 1))`, with default `semantic_weight = 0.85`. Scores rank candidates; they are not probabilities. Selection is deterministic, caps seeds per domain per subquestion, and retains rejected candidates with reasons. Search runs with bounded concurrency; crawls run sequentially to enforce the shared page budget and avoid duplicate fetches across completed research crawls. A failed search or seed crawl leaves other subquestions available. Persisted research tables are created additively alongside existing crawl tables; in-process research jobs abandoned on restart become failed.

The planner never receives search snippets or crawled content, so web text cannot alter the plan in this phase. Inspect `/plan`, `/searches`, and `/sources` for the plan, query/result associations, scores, seed decisions, crawl IDs, and page outcomes. [Architecture details](docs/architecture.md) and the [controlled benchmark labels](backend/tests/fixtures/phase3_benchmarks.json) describe the data and evaluation boundaries.

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
    api/routes/      # health, crawl, research, and retrieval endpoints
    core/            # configuration and JSON logging
    crawler/         # frontier, normalization, safety, fetching, parser, embeddings, scoring
    db/              # SQLAlchemy models, additive migration, repository
    evaluation/      # controlled Phase 2, 3, and 4 metrics
    rag/             # chunking, vector/BM25 indexes, fusion, reranking, evidence
    research/        # Ollama planner, prompt, search provider
    schemas/         # Pydantic API contracts
    services/        # crawl job lifecycle
    main.py
  tests/             # component and API/integration tests
    fixtures/research_site/
  requirements.txt
  requirements-intelligent.txt
  requirements-research.txt
docs/architecture.md
docs/phase2-demo-results.json
pyproject.toml
```

## Set up on Windows PowerShell

From the repository root, with Python 3.11 or newer installed:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-research.txt
ollama pull Qwen3:latest
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

The first intelligent crawl downloads BGE-M3 from Hugging Face and caches it locally. To download and check the model before starting the API:

```powershell
.\.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer; model = SentenceTransformer('BAAI/bge-m3'); print(model.get_embedding_dimension())"
```

This download needs internet once; embedding inference then runs locally. Ollama must be running at `SPIDERMIND_OLLAMA_URL` (default `http://127.0.0.1:11434`); `SPIDERMIND_OLLAMA_MODEL` selects the installed model. To run only FIFO without the model dependency, install `backend\requirements.txt` instead. The default SQLite file is `spidermind.db` in the working directory. Existing Phase 1/2 SQLite files receive an idempotent, additive schema migration on startup. Set `SPIDERMIND_DATABASE_URL` to another `sqlite+aiosqlite:///...` URL if needed. Phase 3 settings include `SPIDERMIND_MAX_CONCURRENT_SEARCHES`, `SPIDERMIND_MAX_SEEDS_PER_DOMAIN_PER_SUBQUESTION`, `SPIDERMIND_SEED_SEMANTIC_WEIGHT`, and `SPIDERMIND_SEARCH_RETRIES`. Crawls currently run one at a time to enforce page budgets even if `SPIDERMIND_MAX_CONCURRENT_SUBQUESTION_CRAWLS` is higher. Crawling defaults to one second between requests to the same domain.

## API

For a Phase 3 research job:

```powershell
$body = @{
  question = "Compare GraphRAG with traditional RAG for enterprise knowledge systems"
  max_subquestions = 6
  search_queries_per_subquestion = 3
  search_results_per_query = 8
  seeds_per_subquestion = 3
  max_pages_per_subquestion = 10
  max_total_pages = 60
  max_depth = 2
} | ConvertTo-Json
$research = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/research -ContentType application/json -Body $body
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/research/$($research.research_job_id)"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/research/$($research.research_job_id)/plan"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/research/$($research.research_job_id)/searches"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/research/$($research.research_job_id)/sources"
```

The research status is `pending`, `running`, `completed`, `partial`, `failed`, or `cancelled`. The `stage` shows planning, searching, scoring, or crawling progress. Inspect the older crawl IDs through `/api/v1/crawl/{id}` and `/links`. Research input limits reject oversized plans, queries, and page budgets with HTTP 422.

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
$demo = Get-Content docs\phase2-demo-results.json -Raw | ConvertFrom-Json
.\.venv\Scripts\python.exe -m app.evaluation.compare --database-url sqlite+aiosqlite:///./spidermind-demo.db --fifo-job $demo.jobs.fifo --intelligent-job $demo.jobs.intelligent --labels-file backend\tests\fixtures\research_site\labels.json
```

## Phase 3 evaluation

`backend/app/evaluation/phase3.py` computes facet coverage from declared alias labels, token-Jaccard redundancy and query diversity (threshold 0.8), schema valid-plan rate, seed precision at K, relevant seed yield, unique-domain ratio, selected/rejected cosine distributions, relevant-page yield, mean page cosine relevance, and page-budget utilization. [Benchmark fixtures](backend/tests/fixtures/phase3_benchmarks.json) contain three realistic questions and high-level expected facets. Matching is explicit and conservative; these are controlled metrics, not a claim of general research accuracy. `backend/tests/test_phase3.py` runs a deterministic end-to-end job through fake planner/search/embeddings, local HTTP fixture pages, real orchestration, and SQLite, then evaluates the persisted output. Live public search quality is separate from this repeatable test.

The measured fixture output is in [phase3-controlled-results.json](docs/phase3-controlled-results.json): 2/2 relevant selected seeds, 3 labeled relevant pages, 4/4 page-attempt slots used, and mean page cosine 0.667. Both seeds share one domain, giving unique-domain ratio 0.5; the domain cap applies within each subquestion.

For an opt-in live check with Ollama, BGE-M3, public search, and at most two one-page crawls:

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m app.evaluation.phase3_smoke
```

A measured live run is stored in [phase3-live-smoke-results.json](docs/phase3-live-smoke-results.json). Ollama produced two valid subquestions; DDGS returned four unique public results; two seeds were selected. One seed's site exceeded the redirect limit, and an arXiv HTML page was crawled and scored. The research job correctly ended `partial` with one useful page. This is a connectivity and integration smoke test, not a relevance benchmark.

## Phase 4 evidence retrieval

Install with `pip install -r backend/requirements-rag.txt`. Phase 4 indexes successful, nonduplicate pages from selected Phase 3 crawl seeds. Start the API with `uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000`. The SQLite startup migration moves schema version 3 to 4 additively; existing crawl and research rows are retained. Local Qdrant persists at `data/qdrant` by default. Model files are fetched and cached on first use.

```powershell
$researchJobId = "<existing research job UUID>"
$indexJob = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/research/$researchJobId/index"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/research/$researchJobId/index"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/research/$researchJobId/index/consistency"
$body = @{
    query = "How does GraphRAG differ from traditional RAG architecture?"
    retrieval_mode = "hybrid"
    dense_top_k = 50
    lexical_top_k = 50
    fusion_top_k = 30
    final_top_k = 8
    rerank = $true
    include_neighbor_context = $true
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/research/$researchJobId/retrieve" -ContentType "application/json" -Body $body
```

Index jobs run in process and expose `PENDING`, `RUNNING`, `COMPLETED`, `PARTIAL`, or `FAILED` plus document, chunk, timing, and error counters. A repeated request while a job is active returns that job. An unchanged completed document is skipped; changed content, title, chunk profile, or embedding model causes reindexing. The consistency endpoint compares active SQLite chunks, FTS rows, and Qdrant point IDs. `source_domain`, `document_id`, `crawl_job_id`, and Phase 3 `subquestion_id` are optional retrieval filters. Subquestion filtering follows selected seed **crawl jobs**: pages reached from a seed can match that subquestion even if that page itself was not a search result for it. Results include scores, ranks, source URLs, page IDs, and bounded neighbor context. Scores are ranking signals, not probabilities.

The chunker uses the BGE-M3 tokenizer and tries paragraph and sentence boundaries before token splits. Defaults are target 500, maximum 650, overlap 80, and minimum 80 tokens. It keeps one canonical passage per research job and stores every document occurrence separately. The shared BGE-M3 provider embeds title, section, and passage into 1024-dimensional vectors. Qdrant stores vectors; SQLite stores relational provenance and an FTS5 BM25 index with triggers. If Python's SQLite lacks FTS5, a local BM25 fallback remains available.

Dense retrieval can find paraphrases. BM25 is useful for exact identifiers, acronyms, model names, and uncommon technical terms. Hybrid retrieval joins the candidate lists by canonical chunk ID with reciprocal rank fusion; it uses ranks because raw cosine and SQLite BM25 scores have different scales. A local `BAAI/bge-reranker-v2-m3` cross encoder scores at most 30 fused pairs by default. A per-document cap is applied after reranking. On this CPU, reranking 30 short fixture passages added about 7 seconds per query; 30 longer live passages took 51 to 66 seconds; `SPIDERMIND_RERANK_ENABLED=false` or `"rerank": false` disables it when latency matters. A requested reranker failure is returned as an error, not silently ignored.

The [controlled fixture](backend/tests/fixtures/phase4_benchmark.json) has eight labeled source documents and eight questions, including exact identifiers, paraphrases, and distractors. Run `python -m app.evaluation.phase4_benchmark --output docs/phase4-benchmark-results.json` with `PYTHONPATH=backend` to compare dense, BM25, hybrid, and hybrid with reranking across four chunk profiles. The [measured artifact](docs/phase4-benchmark-results.json) includes Recall@5, Precision@5, MRR, nDCG@5, HitRate@5, per-query rankings, latency, and index metrics. On the 80-token fixture profile, dense Recall@5 was 0.875, BM25 0.9375, hybrid 0.875, and hybrid with reranking 0.9375. This small corpus does not establish broad superiority; BM25 beat plain hybrid here, and reranking cost about 6.8 seconds per query on this CPU run. The 180- and 500-token profiles both produce eight chunks because these fixture documents are short. [The live smoke artifact](docs/phase4-live-smoke-results.json) indexes a copy of a prior Phase 3 job and records real URLs and snippets; it is a connectivity check, not a relevance benchmark.

See [architecture.md](docs/architecture.md) for the data flow and recovery limits.

## Phase 5 research agent

Install `backend/requirements-agent.txt` and start the API. Run Phase 3 research and Phase 4 indexing before starting the agent. The agent reads every original subquestion, performs hybrid retrieval, applies configurable chunk and independent-source minimums, asks the local Ollama model for structured coverage assessments, and persists missing aspects as prioritized gaps. If evidence is insufficient, it generates at most two queries per gap, deduplicates them against Phase 3 and earlier agent queries, searches with the existing DDGS provider, applies the existing URL safety and semantic seed scoring rules, runs an intelligent crawl with the **gap description** as its query, incrementally indexes the result, and reassesses. Search and crawl actions are recorded with IDs, outcomes, and timings. It stops on sufficient evidence, explicit budgets, cancellation, two stagnant iterations, or a failure that prevents continuation. Coverage minimums are operating rules, not truth or confidence estimates.

The graph uses `StateGraph` with conditional stop routes. Its bounded state contains IDs, evidence counts, budgets, and routing fields. SQLite LangGraph checkpoints live at `data/langgraph-checkpoints.sqlite` by default (`SPIDERMIND_AGENT_CHECKPOINT_PATH`); agent runs, iterations, gaps, evidence assessments, and actions live in the SpiderMind domain database. Startup resumes unfinished checkpointed runs. Phase 5 introduced schema version 5; the current additive database schema version is 7. SQLite checkpointing and in-process task execution suit a single local API worker; distributed deployment needs a worker queue and a server-grade checkpointer.

Retrieved passages are placed in a bounded `UNTRUSTED_EVIDENCE` envelope. Angle brackets in source text are escaped. The Ollama assessor receives only top passages with per-passage and total character limits, plus an instruction to treat all webpage text as data. It has no tool access. The query generator sees the gap description and prior queries, never the full page body. Both use Pydantic JSON schemas, temperature zero, and one repair attempt. No model chain of thought or final research conclusion is stored.

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-agent.txt
ollama pull Qwen3:latest
$env:SPIDERMIND_OLLAMA_MODEL = "Qwen3:latest"
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

In another PowerShell window, after a research job and its initial `/index` job complete:

```powershell
$researchJobId = "<research job UUID>"
$body = @{
  max_iterations = 5
  max_new_search_queries = 12
  max_new_seeds = 10
  max_new_pages = 40
  max_runtime_seconds = 900
  min_evidence_chunks_per_subquestion = 3
  min_unique_sources_per_subquestion = 2
  retrieval_top_k = 8
  rerank = $true
} | ConvertTo-Json
$run = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/research/$researchJobId/agent" -ContentType "application/json" -Body $body
$runId = $run.agent_run_id
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/research/$researchJobId/agent/$runId"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/research/$researchJobId/agent/$runId/iterations"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/research/$researchJobId/agent/$runId/gaps"
# Optional:
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/research/$researchJobId/agent/$runId/cancel"
```

The [controlled Phase 5 results](docs/phase5-agent-results.json) come from a repeatable local fixture with labeled sources. Run `cd backend; ..\.venv\Scripts\python.exe scripts/evaluate_phase5.py` to regenerate them. The [live Phase 5 smoke](docs/phase5-live-smoke-results.json) uses a copy of the prior Phase 3 job, real local models, and public search; it is a connectivity check and is not a labeled benchmark. Run `cd backend; ..\.venv\Scripts\python.exe scripts/live_phase5_smoke.py` to repeat with a new copied database. The smoke caps the agent at one iteration, query, seed, and page. Its initial index step can take several minutes on CPU.

For a local Mermaid graph export, run `cd backend; ..\.venv\Scripts\python.exe scripts/show_agent_graph.py`.

## Phase 6 evidence engine

Install `backend/requirements-evidence.txt`. Once Phase 4 has indexed a research job, `POST /api/v1/research/{job_id}/evidence` starts a separate, bounded evidence job. An optional completed Phase 5 agent run ID links the verification to that run. The engine selects a small source-diverse evidence pack for each subquestion, asks local Qwen3 for structured atomic claims, normalizes and conservatively deduplicates them, then retrieves against the **whole existing research index** using each claim and up to two counterqueries. Phase 6 has no web-search or web-fetch path.

The default local NLI model is `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` on CPU, batch size 2; the larger model is configurable. NLI loads lazily, validates its label IDs from model config, and returns entailment, neutral, and contradiction **model scores**. Numeric, temporal, comparative, close-score, or conflicting pairs can go through structured Qwen3 adjudication. Different explicit years, datasets, or versions prevent a direct support or contradiction decision even if the model disagrees. A requested fallback model is logged and stored; there is no silent switch. Evidence confidence is an ordinal source-based tier, **not a probability that a claim is true**. Retrieval relevance scores never enter that tier. CPU defaults cap a job at four subquestions, five claims each, eight candidates per claim and 1,800 seconds; the API also rejects a request whose maximum claim/evidence pair budget exceeds 2,000.

Supporting and contradicting citations are selected from single sentences or adjacent sentence pairs, required to overlap the claim lexically, checked against persisted chunk text with exact offsets, and linked to claim/evidence relation rows. If a selected passage cannot yield a valid citation, the engine tries the next candidate within the final citation budget. Exact content mirrors and canonical URL matches share a source group, so a repost cannot create independent corroboration. The ledger contains claims, relations, citations, provenance, status and verification metadata. It does not contain final answers or recommendations. Schema version 6 adds the evidence tables without replacing Phase 1–5 records. See [architecture.md](docs/architecture.md) for the decision rules and limits.

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-evidence.txt
ollama pull Qwen3:latest
$env:SPIDERMIND_EVIDENCE_NLI_MODEL = "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

After indexing, in another PowerShell window:

```powershell
$researchJobId = "<research job UUID>"
$agentRunId = "<completed Phase 5 run UUID>"
$body = @{
  agent_run_id = $agentRunId
  max_subquestions = 1
  max_claims_per_subquestion = 2
  evidence_candidates_per_claim = 4
  final_evidence_per_claim = 2
  use_nli = $true
  use_llm_adjudication = $true
  rerank = $false
} | ConvertTo-Json
$job = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/research/$researchJobId/evidence" -ContentType "application/json" -Body $body
$evidenceJobId = $job.evidence_job_id
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/research/$researchJobId/evidence/$evidenceJobId"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/research/$researchJobId/evidence/$evidenceJobId/claims"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/research/$researchJobId/evidence/$evidenceJobId/contradictions"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/research/$researchJobId/evidence/$evidenceJobId/ledger"
```

Run `cd backend; ..\.venv\Scripts\python.exe scripts/evaluate_phase6.py` for the controlled deterministic fixture, and `..\.venv\Scripts\python.exe scripts/evaluate_phase6_nli.py` for the small labeled real-NLI pilot. Run `..\.venv\Scripts\python.exe scripts/evaluate_phase6_hybrid.py` to adjudicate the triggered pilot pairs with local Qwen3. The [controlled results](docs/phase6-evidence-results.json) explicitly separate fake pipeline metrics from [real base-model pilot results](docs/phase6-nli-base-results.json). `scripts/live_phase6_smoke.py` uses a copy of an existing Phase 5 job and produces a separate connectivity artifact. None of these small fixtures establishes broad factual accuracy.

## Phase 7 knowledge and citation graph

Phase 7 projects completed or partial Phase 6 evidence jobs into versioned SQLite graph snapshots. The Phase 1–6 relational rows remain authoritative. The graph builder creates only edges justified by persisted subquestions, claims, claim origins, evidence relations, citations, chunk occurrences, and Phase 6 source groups. There is no LLM, web request, speculative claim-to-claim edge, Neo4j, or required NetworkX dependency in graph construction. The in-memory `GraphProjection` is a directed multigraph for bounded traversal, analytics, and JSON export; SQLite `graph_snapshots`, `graph_build_jobs`, `graph_nodes`, and `graph_edges` are its durable representation. Database schema version is 7; graph schema version is 1.

Node types are `RESEARCH`, `SUBQUESTION`, `CLAIM`, `CITATION`, `EVIDENCE_CHUNK`, `DOCUMENT`, and `SOURCE_GROUP`. Edge types are `HAS_SUBQUESTION`, `HAS_CLAIM`, `ORIGINATED_FROM`, `SUPPORTED_BY`, `PARTIALLY_SUPPORTED_BY`, `CONTRADICTED_BY`, `HAS_CITATION`, `CITES_CHUNK`, `PART_OF_DOCUMENT`, and `MEMBER_OF_SOURCE_GROUP`. Origin and verified support can coexist between one claim and chunk. A support or contradiction edge records whether a valid Phase 6 citation exists; uncited relations never create fabricated citation nodes. Canonical chunks may point to multiple document occurrences. Source groups preserve Phase 6 independence grouping; contribution counts are structural, not credibility scores.

Graph builds fingerprint the source rows and configuration. An unchanged completed build reuses its snapshot; changed evidence creates a new snapshot with a new content hash. Construction writes nodes and edges in one SQLite transaction, then checks endpoints, exact citation spans, source membership, cross-job scope, status/confidence fidelity, expected rows/edges, and provenance paths. Invalid materializations remain inspectable through status and consistency, while graph data endpoints serve only validated completed snapshots. Startup marks interrupted graph jobs partial and a new POST retries them. JSON export sorts nodes and edges by deterministic IDs and excludes vectors and full chunk bodies by default; `SPIDERMIND_GRAPH_EXPORT_INCLUDE_CHUNK_TEXT=true` adds at most 1,200 characters per chunk as an excerpt. GraphML is not implemented.

From the repository root in PowerShell, after a Phase 6 evidence job finishes:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-graph.txt
$env:PYTHONPATH = 'backend'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

In another PowerShell window:

```powershell
$researchJobId = '<research-job-id>'
$evidenceJobId = '<phase6-evidence-job-id>'
$body = @{ evidence_job_id = $evidenceJobId; include_uncited_relations = $true } | ConvertTo-Json
$graphJob = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/research/$researchJobId/graph" -ContentType 'application/json' -Body $body
$snapshotId = $graphJob.graph_snapshot_id
$base = "http://127.0.0.1:8000/api/v1/research/$researchJobId/graph/$snapshotId"
Invoke-RestMethod $base
Invoke-RestMethod "$base/summary"
Invoke-RestMethod "$base/nodes?node_type=CLAIM"
Invoke-RestMethod "$base/claims/<claim-id>"
Invoke-RestMethod "$base/contradictions"
Invoke-RestMethod "${base}/path?from_node_id=research:${researchJobId}&to_node_id=claim:<claim-id>"
Invoke-RestMethod "$base/consistency"
Invoke-RestMethod "$base/export?format=json"
```

Additional graph endpoints list/filter edges, inspect node detail (chunk text is opt-in), and traverse a bounded `/subgraph` with `root_node_id`, `depth`, `direction`, `node_types`, `edge_types`, and `max_nodes`. Node/edge lists use `limit` and `offset`. The controlled [Phase 7 result](docs/phase7-graph-results.json) uses a local fixture with all six claim statuses, mirrored documents, two independent source groups, valid citations, and an uncited relation. The [live smoke](docs/phase7-live-smoke-results.json) builds from a copy of the final successful Phase 6 database; it is not a labeled graph accuracy benchmark. Reproduce them with `$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe backend/scripts/evaluate_phase7.py` and `backend/scripts/live_phase7_smoke.py` respectively.

## Tests and checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m ruff format --check backend
.\.venv\Scripts\python.exe -m compileall -q backend\app
```

The suite uses HTTPX mock transports and temporary SQLite databases, with no dependency on public websites or Ollama. Phase 5 tests cover no-gap stopping, budgets, stagnation, fixture crawl/index acquisition, prompt-envelope escaping, checkpoint resume, and failed plan handling.

## Current limits and roadmap

Jobs run as in-process tasks. The Phase 5 agent resumes from its SQLite LangGraph checkpoint on restart; earlier research and index jobs retain their separate interruption behavior. There is no distributed worker scheduling. The SQLite migration is additive, not a general migration framework. BGE-M3, BGE-reranker-v2-m3, and Qwen3 have substantial memory and startup costs. Search quality and availability depend on public engines used by `ddgs`; timeout or rate limiting can produce partial jobs. The controlled Phase 5 assessor is deterministic and does not establish real-world coverage accuracy. A threshold may exclude useful low-scoring pages, and link context can be sparse or misleading. The crawler does not run JavaScript, authenticate to sites, enforce site-specific crawl-delay directives, or parse sitemaps. Robots retrieval fails closed when unavailable, except 404/410. The URL guard rejects local/private and non-global resolved IPs, validates every redirect hop, and disables environment proxies. DNS validation and connection resolution are separate, so DNS rebinding remains a known SSRF limit; production exposure to untrusted users needs connection-level address pinning and network egress controls. Retrieved passages may contain prompt injection; Phase 5 treats them as untrusted data but model output still requires independent review. Final answer generation remains future work. Phase 6 verifies evidence provisionally; real NLI can misclassify passages, and exact citation rules may leave a claim unresolved. Phase 7 projects those provisional records; graph integrity does not establish factual truth.
