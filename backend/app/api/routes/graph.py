"""Phase 7 graph HTTP API; semantics live in graph services."""

import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.graph.models import GraphBuildJob
from app.graph.types import EdgeType, NodeType

router = APIRouter(prefix="/api/v1/research", tags=["graph"])


class GraphBuildRequest(BaseModel):
    evidence_job_id: UUID
    include_uncited_relations: bool | None = None


async def _graph(request: Request, research_job_id: UUID, snapshot_id: UUID):
    try:
        return await request.app.state.graph_query_service.load(
            str(research_job_id), str(snapshot_id)
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{research_job_id}/graph", status_code=status.HTTP_202_ACCEPTED)
async def build_graph(research_job_id: UUID, payload: GraphBuildRequest, request: Request) -> dict:
    try:
        job, snapshot = await request.app.state.graph_service.start(
            str(research_job_id),
            str(payload.evidence_job_id),
            payload.include_uncited_relations,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "graph_job_id": job.id,
        "graph_snapshot_id": snapshot.id,
        "status": snapshot.status.lower(),
    }


@router.get("/{research_job_id}/graph/{snapshot_id}")
async def graph_status(research_job_id: UUID, snapshot_id: UUID, request: Request) -> dict:
    service = request.app.state.graph_service
    try:
        row = await service.snapshot(str(research_job_id), str(snapshot_id))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    async with service.sessions() as session:
        job = (
            (
                await session.execute(
                    select(GraphBuildJob)
                    .where(GraphBuildJob.snapshot_id == row.id)
                    .order_by(GraphBuildJob.started_at.desc(), GraphBuildJob.id.desc())
                )
            )
            .scalars()
            .first()
        )
    metrics = json.loads(row.metrics_json)
    return {
        "graph_snapshot_id": row.id,
        "graph_job_id": job.id if job else None,
        "research_job_id": row.research_job_id,
        "evidence_job_id": row.evidence_job_id,
        "status": row.status,
        "graph_schema_version": row.graph_schema_version,
        "input_fingerprint": row.input_fingerprint,
        "graph_content_hash": row.graph_content_hash,
        "node_count": row.node_count,
        "edge_count": row.edge_count,
        "nodes_by_type": json.loads(row.nodes_by_type_json),
        "edges_by_type": json.loads(row.edges_by_type_json),
        "validation_status": row.validation_status,
        "validation_errors": json.loads(row.validation_issues_json),
        "connected_components": metrics.get("connected_components"),
        "orphan_nodes": metrics.get("orphan_node_count"),
        "orphan_edges": metrics.get("orphan_edge_count"),
        "timings": json.loads(row.timings_json),
        "created_at": row.created_at,
        "completed_at": row.completed_at,
        "duration_ms": row.duration_ms,
        "build_job": {
            "status": job.status,
            "nodes_created": job.nodes_created,
            "edges_created": job.edges_created,
            "nodes_reused": job.nodes_reused,
            "edges_reused": job.edges_reused,
            "validation_errors": job.validation_errors,
            "error_message": job.error_message,
        }
        if job
        else None,
    }


@router.get("/{research_job_id}/graph/{snapshot_id}/nodes")
async def graph_nodes(
    research_job_id: UUID,
    snapshot_id: UUID,
    request: Request,
    node_type: NodeType | None = None,
    subquestion_id: str | None = None,
    claim_status: str | None = None,
    confidence: str | None = None,
    source_domain: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    _, graph = await _graph(request, research_job_id, snapshot_id)
    return request.app.state.graph_query_service.list_nodes(
        graph,
        node_type=node_type,
        subquestion_id=subquestion_id,
        claim_status=claim_status,
        confidence=confidence,
        source_domain=source_domain,
        limit=limit,
        offset=offset,
    )


@router.get("/{research_job_id}/graph/{snapshot_id}/nodes/{node_id:path}")
async def graph_node_detail(
    research_job_id: UUID,
    snapshot_id: UUID,
    node_id: str,
    request: Request,
    include_text: bool = False,
) -> dict:
    _, graph = await _graph(request, research_job_id, snapshot_id)
    try:
        return await request.app.state.graph_query_service.node_detail(
            graph,
            node_id,
            include_text=include_text,
            research_job_id=str(research_job_id),
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{research_job_id}/graph/{snapshot_id}/edges")
async def graph_edges(
    research_job_id: UUID,
    snapshot_id: UUID,
    request: Request,
    edge_type: EdgeType | None = None,
    source_node_id: str | None = None,
    target_node_id: str | None = None,
    claim_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    _, graph = await _graph(request, research_job_id, snapshot_id)
    return request.app.state.graph_query_service.list_edges(
        graph,
        edge_type=edge_type,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        claim_id=claim_id,
        limit=limit,
        offset=offset,
    )


@router.get("/{research_job_id}/graph/{snapshot_id}/subgraph")
async def graph_subgraph(
    research_job_id: UUID,
    snapshot_id: UUID,
    request: Request,
    root_node_id: str,
    depth: int = Query(default=2, ge=0, le=4),
    direction: Literal["outgoing", "incoming", "both"] = "both",
    node_types: Annotated[list[NodeType] | None, Query()] = None,
    edge_types: Annotated[list[EdgeType] | None, Query()] = None,
    max_nodes: int = Query(default=100, ge=1, le=1000),
) -> dict:
    settings = request.app.state.graph_service.settings
    if depth > settings.graph_max_traversal_depth or max_nodes > settings.graph_max_subgraph_nodes:
        raise HTTPException(422, "Graph traversal limit exceeded")
    _, graph = await _graph(request, research_job_id, snapshot_id)
    try:
        return graph.neighborhood(
            root_node_id,
            depth=depth,
            direction=direction,
            node_types=set(node_types) if node_types else None,
            edge_types=set(edge_types) if edge_types else None,
            max_nodes=max_nodes,
            max_edges=settings.graph_max_subgraph_edges,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{research_job_id}/graph/{snapshot_id}/path")
async def graph_path(
    research_job_id: UUID,
    snapshot_id: UUID,
    request: Request,
    from_node_id: str,
    to_node_id: str,
    max_hops: int = Query(default=8, ge=1, le=12),
) -> dict:
    settings = request.app.state.graph_service.settings
    if max_hops > settings.graph_max_path_hops:
        raise HTTPException(422, "Graph path limit exceeded")
    _, graph = await _graph(request, research_job_id, snapshot_id)
    try:
        paths = graph.shortest_paths(
            from_node_id,
            to_node_id,
            max_hops=max_hops,
            max_paths=settings.graph_max_paths,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"paths": paths, "count": len(paths)}


@router.get("/{research_job_id}/graph/{snapshot_id}/claims/{claim_id}")
async def graph_claim(
    research_job_id: UUID, snapshot_id: UUID, claim_id: str, request: Request
) -> dict:
    _, graph = await _graph(request, research_job_id, snapshot_id)
    try:
        return request.app.state.graph_query_service.claim_view(graph, claim_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{research_job_id}/graph/{snapshot_id}/contradictions")
async def graph_contradictions(research_job_id: UUID, snapshot_id: UUID, request: Request) -> dict:
    _, graph = await _graph(request, research_job_id, snapshot_id)
    return request.app.state.graph_query_service.contradictions(graph)


@router.get("/{research_job_id}/graph/{snapshot_id}/summary")
async def graph_summary(research_job_id: UUID, snapshot_id: UUID, request: Request) -> dict:
    _, graph = await _graph(request, research_job_id, snapshot_id)
    return request.app.state.graph_query_service.summary(graph)


@router.get("/{research_job_id}/graph/{snapshot_id}/consistency")
async def graph_consistency(research_job_id: UUID, snapshot_id: UUID, request: Request) -> dict:
    try:
        return await request.app.state.graph_service.consistency(
            str(research_job_id), str(snapshot_id)
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{research_job_id}/graph/{snapshot_id}/export")
async def graph_export(
    research_job_id: UUID,
    snapshot_id: UUID,
    request: Request,
    format: Literal["json"] = "json",
) -> dict:
    snapshot, graph = await _graph(request, research_job_id, snapshot_id)
    return await request.app.state.graph_query_service.export_with_optional_text(graph, snapshot)
