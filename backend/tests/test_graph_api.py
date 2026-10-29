"""Graph API scope, traversal bounds, and inspectable response contracts."""

import pytest
from app.core.config import Settings
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from tests.graph_fixture import seed_graph_fixture


class FakeEmbedding:
    model_name = "fixture"
    load_time_ms = 0

    async def embed(self, text):
        raise AssertionError("Graph API must not call embeddings")

    async def embed_many(self, texts):
        raise AssertionError("Graph API must not call embeddings")


class FakeVector:
    async def close(self):
        pass


class FakeReranker:
    async def score(self, query, passages):
        raise AssertionError("Graph API must not call reranker")


@pytest.mark.asyncio
async def test_graph_api_build_scope_queries_consistency_and_export(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
        agent_checkpoint_path=str(tmp_path / "checkpoints.sqlite"),
        qdrant_path=str(tmp_path / "qdrant"),
        graph_max_subgraph_nodes=20,
    )
    app = create_app(
        settings,
        embedding_provider=FakeEmbedding(),
        vector_index=FakeVector(),
        reranker=FakeReranker(),
    )
    async with app.router.lifespan_context(app):
        ids = await seed_graph_fixture(app.state.graph_service.sessions)
        base = f"/api/v1/research/{ids['research_job_id']}/graph"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/ready")).status_code == 200
            created = await client.post(base, json={"evidence_job_id": ids["evidence_job_id"]})
            assert created.status_code == 202, created.text
            snapshot_id = created.json()["graph_snapshot_id"]
            await app.state.graph_service.tasks.pop()
            url = f"{base}/{snapshot_id}"
            status = (await client.get(url)).json()
            assert status["status"] == "COMPLETED"
            assert status["validation_status"] == "VALID"
            assert status["node_count"] == 24 and status["edge_count"] == 42
            assert (await client.get(f"{url}/nodes", params={"node_type": "CLAIM"})).json()[
                "total"
            ] == 6
            assert (
                await client.get(f"{url}/edges", params={"edge_type": "CONTRADICTED_BY"})
            ).json()["total"] == 2
            claim = (await client.get(f"{url}/claims/{ids['claims']['CONTESTED']}")).json()
            assert claim["status"] == "CONTESTED"
            assert claim["supporting_chunks"] and claim["contradicting_chunks"]
            assert (await client.get(f"{url}/contradictions")).json()["claims"]
            assert (await client.get(f"{url}/summary")).json()["claims_total"] == 6
            assert (await client.get(f"{url}/consistency")).json()["status"] == "VALID"
            assert (
                await client.get(
                    f"{url}/subgraph",
                    params={
                        "root_node_id": f"claim:{ids['claims']['CONTESTED']}",
                        "depth": 5,
                    },
                )
            ).status_code == 422
            assert (
                await client.get(
                    f"{url}/subgraph",
                    params={
                        "root_node_id": f"claim:{ids['claims']['CONTESTED']}",
                        "max_nodes": 21,
                    },
                )
            ).status_code == 422
            path = (
                await client.get(
                    f"{url}/path",
                    params={
                        "from_node_id": f"research:{ids['research_job_id']}",
                        "to_node_id": f"claim:{ids['claims']['CONTESTED']}",
                    },
                )
            ).json()
            assert path["count"] >= 1
            assert len(path["paths"][0]["edges"]) == 2
            exported = (await client.get(f"{url}/export", params={"format": "json"})).json()
            assert len(exported["nodes"]) == 24
            assert all("text" not in node["properties"] for node in exported["nodes"])
            assert (
                await client.get(f"{url}/export", params={"format": "graphml"})
            ).status_code == 422
            assert (
                await client.get(
                    f"/api/v1/research/{ids['other_research_job_id']}/graph/{snapshot_id}"
                )
            ).status_code == 404
            assert (
                await client.post(
                    f"/api/v1/research/{ids['other_research_job_id']}/graph",
                    json={"evidence_job_id": ids["evidence_job_id"]},
                )
            ).status_code == 404
