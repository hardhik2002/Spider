"""Bounded real Phase 3-7 API smoke; writes observed outcomes, including failures."""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

BASE = os.environ.get("SPIDERMIND_WORKFLOW_API_BASE", "http://127.0.0.1:8000/api/v1/research")
OUT = Path(os.environ.get("SPIDERMIND_WORKFLOW_REPORT", "docs/phase8a-live-workflow-results.json"))
QUESTION = "What is hybrid retrieval in RAG?"


def now():
    return datetime.now(UTC).isoformat()


def main():
    report = {"question": QUESTION, "started_at": now(), "steps": [], "outcome": "RUNNING"}
    started = time.monotonic()

    def save():
        report["elapsed_seconds"] = round(time.monotonic() - started, 1)
        OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def request(client, method, path, payload=None):
        for attempt in range(3 if method == "GET" else 1):
            try:
                response = client.request(method, BASE + path, json=payload, timeout=30)
                response.raise_for_status()
                return response.json()
            except (httpx.TransportError, httpx.HTTPStatusError):
                if method != "GET" or attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))

    def poll(client, name, path, limit=360):
        until = time.monotonic() + limit
        previous = None
        while time.monotonic() < until:
            data = request(client, "GET", path)
            state = data["status"].upper()
            signature = (state, data.get("stage"), data.get("current_node"))
            if signature != previous:
                report["steps"].append(
                    {
                        "at": now(),
                        "step": name,
                        "status": state,
                        "stage": data.get("stage"),
                        "node": data.get("current_node"),
                    }
                )
                print(name, state, data.get("stage"), data.get("current_node"), flush=True)
                save()
                previous = signature
            if state in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "BUDGET_EXHAUSTED"}:
                return data
            time.sleep(3)
        raise TimeoutError(f"{name} exceeded {limit} seconds")

    try:
        with httpx.Client() as client:
            rid = os.environ.get("SPIDERMIND_WORKFLOW_RESEARCH_ID")
            if not rid:
                research = request(
                    client,
                    "POST",
                    "",
                    {
                        "question": QUESTION,
                        "max_subquestions": 1,
                        "search_queries_per_subquestion": 1,
                        "search_results_per_query": 4,
                        "seeds_per_subquestion": 3,
                        "max_pages_per_subquestion": 2,
                        "max_total_pages": 6,
                        "max_depth": 0,
                    },
                )
                rid = research["research_job_id"]
            report["research_job_id"] = rid
            save()
            research = poll(client, "research", f"/{rid}", 360)
            report["research_final"] = {
                k: research.get(k)
                for k in (
                    "status",
                    "stage",
                    "total_search_results",
                    "selected_seed_count",
                    "pages_crawled",
                    "error_message",
                )
            }
            if (
                research["status"].upper() not in {"COMPLETED", "PARTIAL"}
                or research["pages_crawled"] < 1
            ):
                raise RuntimeError("Research produced no usable crawled pages")
            index = request(client, "POST", f"/{rid}/index")
            report["index_job_id"] = index["index_job_id"]
            save()
            index = poll(client, "index", f"/{rid}/index", 360)
            report["index_final"] = {
                k: index.get(k)
                for k in (
                    "status",
                    "documents_indexed",
                    "chunks_created",
                    "lexical_records",
                    "error_message",
                )
            }
            if (
                index["status"].upper() not in {"COMPLETED", "PARTIAL"}
                or index.get("lexical_records", 0) < 1
            ):
                raise RuntimeError("Index produced no searchable chunks")
            agent = request(
                client,
                "POST",
                f"/{rid}/agent",
                {
                    "max_iterations": 1,
                    "max_new_search_queries": 0,
                    "max_new_seeds": 0,
                    "max_new_pages": 0,
                    "max_runtime_seconds": 120,
                    "min_evidence_chunks_per_subquestion": 1,
                    "min_unique_sources_per_subquestion": 1,
                    "rerank": False,
                },
            )
            aid = agent["agent_run_id"]
            report["agent_run_id"] = aid
            save()
            agent = poll(client, "agent", f"/{rid}/agent/{aid}", 180)
            report["agent_final"] = {
                k: agent.get(k) for k in ("status", "stop_reason", "error_message")
            }
            if agent["status"].upper() not in {"COMPLETED", "PARTIAL", "BUDGET_EXHAUSTED"}:
                raise RuntimeError("Agent did not finish with usable evidence")
            evidence = request(
                client,
                "POST",
                f"/{rid}/evidence",
                {
                    "agent_run_id": aid,
                    "max_subquestions": 1,
                    "max_claims_per_subquestion": 2,
                    "evidence_candidates_per_claim": 3,
                    "final_evidence_per_claim": 2,
                    "max_runtime_seconds": 300,
                    "use_llm_adjudication": False,
                    "use_counterqueries": False,
                    "rerank": False,
                    "max_claim_extraction_chunks": 2,
                },
            )
            eid = evidence["evidence_job_id"]
            report["evidence_job_id"] = eid
            save()
            evidence = poll(client, "evidence", f"/{rid}/evidence/{eid}", 360)
            report["evidence_final"] = {
                k: evidence.get(k)
                for k in ("status", "claims_extracted", "citations_validated", "error")
            }
            if evidence["status"].upper() not in {"COMPLETED", "PARTIAL"}:
                raise RuntimeError("Evidence verification failed")
            graph = request(client, "POST", f"/{rid}/graph", {"evidence_job_id": eid})
            sid = graph["graph_snapshot_id"]
            report["graph_snapshot_id"] = sid
            save()
            graph = poll(client, "graph", f"/{rid}/graph/{sid}", 120)
            report["graph_final"] = {
                k: graph.get(k) for k in ("status", "validation_status", "node_count", "edge_count")
            }
            report["outcome"] = "COMPLETED" if graph["status"] == "COMPLETED" else "PARTIAL"
            save()
    except Exception as exc:
        report["outcome"] = "STOPPED"
        report["error"] = f"{type(exc).__name__}: {exc}"
        save()
        print(report["error"], flush=True)
    report["finished_at"] = now()
    save()


if __name__ == "__main__":
    main()
