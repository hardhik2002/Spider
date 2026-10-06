"""Small opt-in live planner/search/crawl smoke test with bounded inputs."""

import argparse
import asyncio
import json

import httpx

from app.core.config import Settings
from app.main import create_app


async def run(question: str, database_url: str) -> None:
    app = create_app(Settings(database_url=database_url))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                "/api/v1/research",
                json={
                    "question": question,
                    "max_subquestions": 2,
                    "search_queries_per_subquestion": 1,
                    "search_results_per_query": 2,
                    "seeds_per_subquestion": 1,
                    "max_pages_per_subquestion": 1,
                    "max_total_pages": 2,
                    "max_depth": 0,
                },
            )
            created.raise_for_status()
            job_id = created.json()["research_job_id"]
            print(
                json.dumps({"research_job_id": job_id, "initial_status": created.json()["status"]})
            )
            for _ in range(600):
                status = (await client.get(f"/api/v1/research/{job_id}")).json()
                if status["status"] in {"completed", "partial", "failed", "cancelled"}:
                    break
                await asyncio.sleep(1)
            plan = (await client.get(f"/api/v1/research/{job_id}/plan")).json()
            searches = (await client.get(f"/api/v1/research/{job_id}/searches")).json()
            sources = (await client.get(f"/api/v1/research/{job_id}/sources")).json()
            print(
                json.dumps(
                    {
                        "status": status,
                        "plan": plan["plan"],
                        "queries": [
                            {
                                "query": row["query"],
                                "status": row["status"],
                                "result_count": row["result_count"],
                                "error_message": row["error_message"],
                            }
                            for row in searches["queries"]
                        ],
                        "seeds": [
                            {
                                "url": row["url"],
                                "score": row["seed_score"],
                                "selected": row["selected"],
                                "crawl_status": row["crawl_status"],
                            }
                            for row in sources["sources"]
                        ],
                    },
                    indent=2,
                    default=str,
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", default="Compare GraphRAG and traditional RAG architectures")
    parser.add_argument(
        "--database-url", default="sqlite+aiosqlite:///./spidermind-phase3-smoke.db"
    )
    args = parser.parse_args()
    asyncio.run(run(args.question, args.database_url))


if __name__ == "__main__":
    main()
