"""SQLite-backed graph store. All methods scope reads/writes to a snapshot."""

import json
from typing import Protocol

from sqlalchemy import delete, select

from app.graph.models import GraphBuildJob, GraphEdge, GraphNode, GraphSnapshot


def node_record(row: GraphNode) -> dict:
    return {
        "id": row.node_id,
        "type": row.node_type,
        "label": row.label,
        "properties": json.loads(row.properties_json),
    }


def edge_record(row: GraphEdge) -> dict:
    return {
        "id": row.edge_id,
        "type": row.edge_type,
        "source": row.source_node_id,
        "target": row.target_node_id,
        "properties": json.loads(row.properties_json),
    }


class GraphStore(Protocol):
    async def create_snapshot(self, snapshot: GraphSnapshot) -> GraphSnapshot: ...
    async def upsert_nodes(self, snapshot_id: str, nodes: dict[str, dict]) -> tuple[int, int]: ...
    async def upsert_edges(self, snapshot_id: str, edges: dict[str, dict]) -> tuple[int, int]: ...
    async def get_node(self, snapshot_id: str, node_id: str) -> dict | None: ...
    async def list_nodes(self, snapshot_id: str) -> list[dict]: ...
    async def list_edges(self, snapshot_id: str) -> list[dict]: ...
    async def get_neighbors(self, snapshot_id: str, node_id: str) -> list[dict]: ...
    async def delete_snapshot(self, snapshot_id: str) -> None: ...


class SQLiteGraphStore:
    def __init__(self, sessions) -> None:
        self.sessions = sessions

    async def create_snapshot(self, snapshot: GraphSnapshot) -> GraphSnapshot:
        async with self.sessions() as session:
            session.add(snapshot)
            await session.commit()
            return snapshot

    async def get_snapshot(self, snapshot_id: str) -> GraphSnapshot | None:
        async with self.sessions() as session:
            return await session.get(GraphSnapshot, snapshot_id)

    async def find_fingerprint(
        self, research_job_id: str, evidence_job_id: str, fingerprint: str
    ) -> GraphSnapshot | None:
        async with self.sessions() as session:
            return (
                await session.execute(
                    select(GraphSnapshot).where(
                        GraphSnapshot.research_job_id == research_job_id,
                        GraphSnapshot.evidence_job_id == evidence_job_id,
                        GraphSnapshot.input_fingerprint == fingerprint,
                    )
                )
            ).scalar_one_or_none()

    async def upsert_nodes(self, snapshot_id: str, nodes: dict[str, dict]) -> tuple[int, int]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(select(GraphNode).where(GraphNode.snapshot_id == snapshot_id))
            ).all()
            existing = {row.node_id: row for row in rows}
            created = 0
            for key in sorted(nodes):
                item = nodes[key]
                if key in existing:
                    existing[key].node_type = item["type"]
                    existing[key].label = item["label"]
                    existing[key].properties_json = json.dumps(item["properties"], sort_keys=True)
                    continue
                session.add(
                    GraphNode(
                        snapshot_id=snapshot_id,
                        node_id=key,
                        node_type=item["type"],
                        source_entity_id=key.split(":", 1)[1],
                        label=item["label"],
                        properties_json=json.dumps(item["properties"], sort_keys=True),
                    )
                )
                created += 1
            await session.commit()
            return created, len(nodes) - created

    async def upsert_edges(self, snapshot_id: str, edges: dict[str, dict]) -> tuple[int, int]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(select(GraphEdge).where(GraphEdge.snapshot_id == snapshot_id))
            ).all()
            existing = {row.edge_id: row for row in rows}
            created = 0
            for key in sorted(edges):
                item = edges[key]
                if key in existing:
                    existing[key].edge_type = item["type"]
                    existing[key].source_node_id = item["source"]
                    existing[key].target_node_id = item["target"]
                    existing[key].source_entity_id = item["properties"]["source_entity_id"]
                    existing[key].properties_json = json.dumps(item["properties"], sort_keys=True)
                    continue
                props = item["properties"]
                session.add(
                    GraphEdge(
                        snapshot_id=snapshot_id,
                        edge_id=key,
                        edge_type=item["type"],
                        source_node_id=item["source"],
                        target_node_id=item["target"],
                        source_entity_id=props["source_entity_id"],
                        properties_json=json.dumps(props, sort_keys=True),
                    )
                )
                created += 1
            await session.commit()
            return created, len(edges) - created

    async def replace(self, snapshot_id: str, nodes: dict[str, dict], edges: dict[str, dict]):
        """Replace one derived snapshot in one transaction, repairing interrupted builds."""
        async with self.sessions() as session:
            old_nodes = (
                await session.scalars(
                    select(GraphNode.node_id).where(GraphNode.snapshot_id == snapshot_id)
                )
            ).all()
            old_edges = (
                await session.scalars(
                    select(GraphEdge.edge_id).where(GraphEdge.snapshot_id == snapshot_id)
                )
            ).all()
            await session.execute(delete(GraphEdge).where(GraphEdge.snapshot_id == snapshot_id))
            await session.execute(delete(GraphNode).where(GraphNode.snapshot_id == snapshot_id))
            session.add_all(
                [
                    GraphNode(
                        snapshot_id=snapshot_id,
                        node_id=key,
                        node_type=item["type"],
                        source_entity_id=key.split(":", 1)[1],
                        label=item["label"],
                        properties_json=json.dumps(item["properties"], sort_keys=True),
                    )
                    for key, item in sorted(nodes.items())
                ]
            )
            session.add_all(
                [
                    GraphEdge(
                        snapshot_id=snapshot_id,
                        edge_id=key,
                        edge_type=item["type"],
                        source_node_id=item["source"],
                        target_node_id=item["target"],
                        source_entity_id=item["properties"]["source_entity_id"],
                        properties_json=json.dumps(item["properties"], sort_keys=True),
                    )
                    for key, item in sorted(edges.items())
                ]
            )
            await session.commit()
            return {
                "nodes_created": len(set(nodes) - set(old_nodes)),
                "nodes_reused": len(set(nodes) & set(old_nodes)),
                "edges_created": len(set(edges) - set(old_edges)),
                "edges_reused": len(set(edges) & set(old_edges)),
            }

    async def get_node(self, snapshot_id: str, node_id: str) -> dict | None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(GraphNode).where(
                        GraphNode.snapshot_id == snapshot_id, GraphNode.node_id == node_id
                    )
                )
            ).scalar_one_or_none()
            return node_record(row) if row else None

    async def list_nodes(self, snapshot_id: str) -> list[dict]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(GraphNode)
                    .where(GraphNode.snapshot_id == snapshot_id)
                    .order_by(GraphNode.node_id)
                )
            ).all()
            return [node_record(row) for row in rows]

    async def list_edges(self, snapshot_id: str) -> list[dict]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(GraphEdge)
                    .where(GraphEdge.snapshot_id == snapshot_id)
                    .order_by(GraphEdge.edge_id)
                )
            ).all()
            return [edge_record(row) for row in rows]

    async def get_neighbors(self, snapshot_id: str, node_id: str) -> list[dict]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(GraphEdge)
                    .where(
                        GraphEdge.snapshot_id == snapshot_id,
                        (GraphEdge.source_node_id == node_id)
                        | (GraphEdge.target_node_id == node_id),
                    )
                    .order_by(GraphEdge.edge_id)
                )
            ).all()
            return [edge_record(row) for row in rows]

    async def delete_snapshot(self, snapshot_id: str) -> None:
        async with self.sessions() as session:
            await session.execute(delete(GraphEdge).where(GraphEdge.snapshot_id == snapshot_id))
            await session.execute(delete(GraphNode).where(GraphNode.snapshot_id == snapshot_id))
            await session.execute(
                delete(GraphBuildJob).where(GraphBuildJob.snapshot_id == snapshot_id)
            )
            await session.execute(delete(GraphSnapshot).where(GraphSnapshot.id == snapshot_id))
            await session.commit()
