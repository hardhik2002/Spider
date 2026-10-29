# SpiderMind interactive graph UI (Phase 8A)

React, TypeScript, Vite, and React Flow display the existing Phase 7 knowledge/citation graph. The client reads persisted graph records; it does not create claims, change evidence status, or generate answers.

## Required assets

Place the supplied `spider-reference.png` and `spidermind-ui-reference.png` in `src/assets/`. The spider image is rendered by `SpiderHub`; the UI reference is used only for design review. If the spider image is absent, the workspace explicitly marks the asset as pending.

## Start locally

From the repository root, start the backend with the Phase 7 dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-graph.txt
$env:PYTHONPATH = 'backend'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

In another PowerShell window:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api`, `/health`, and `/ready` to `127.0.0.1:8000`, so no backend CORS change is required for the local setup. For deployment, set `VITE_API_BASE_URL` in `.env` to the API origin, or serve the built UI and API from one origin. The production backend does not serve frontend files in this phase.

## Load or build a graph

The startup form accepts a **Research Job ID** and **Graph Snapshot ID**. It can also build a graph from a finished **Evidence Job ID**, polling the asynchronous job until completion. There is no global research-job or snapshot listing API, so the client does not invent one. The last opened IDs are saved locally in the browser and can also be opened at `/research/{researchJobId}/graph/{snapshotId}`.

For an offline local smoke using copied Phase 6 data:

```powershell
$env:PYTHONPATH = 'backend'
.\.venv\Scripts\python.exe backend\scripts\prepare_phase8a_ui_smoke.py
$env:SPIDERMIND_DATABASE_URL = 'sqlite+aiosqlite:///./data/phase8a-ui-smoke.db'
$env:SPIDERMIND_QDRANT_PATH = 'data/phase8a-qdrant'
$env:SPIDERMIND_AGENT_CHECKPOINT_PATH = 'data/phase8a-checkpoints.sqlite'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

The preparation command prints the real research and snapshot IDs. It copies an existing Phase 6 database; it does not search the web or call an LLM.

## Graph interactions

- Initial data comes from `/subgraph` rooted at the research node, depth 2, at most 100 nodes. Double-click a node to expand another bounded neighborhood. The UI merges nodes and semantic edges by their Phase 7 IDs.
- Click a node to inspect its API detail. Claims also load `/claims/{claim_id}` with supporting, partial, contradicting, citation, document, and source-group views. Citation spans are displayed exactly as stored.
- Search scans currently loaded node labels and text fields. Sidebar sections and graph filters change the loaded view; they do not query an LLM. The contradictions drawer reads `/contradictions`.
- Directed paths come from `/path`; the selected path is highlighted. `/summary` supplies real counts, and `/consistency` supplies the validation indicator.
- Zoom, pan, fit, node labels, web background, and keyboard-accessible controls operate locally. The spider is a decorative visual hub, never a Phase 7 graph node.

## Quality gates

```powershell
npm run lint
npm run test
npm run build
```

Tests use controlled Phase 7 response fixtures and do not require a live backend. The live UI smoke requires a running backend and the supplied spider image for visual approval. The React Flow canvas loads one bounded subgraph at a time; very large graph exploration would benefit from server-side search and viewport-aware paging.

With the copied live backend running, the optional API-client integration test is:

```powershell
$env:VITE_LIVE_TEST = '1'
$env:VITE_LIVE_RESEARCH_ID = '<research-job-id printed by preparation script>'
$env:VITE_LIVE_SNAPSHOT_ID = '<graph-snapshot-id printed by preparation script>'
npm run test
```
