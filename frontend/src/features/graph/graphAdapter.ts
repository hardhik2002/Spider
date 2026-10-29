import type { Edge, Node } from '@xyflow/react'
import type { ClaimStatus, ConfidenceTier, GraphEdgeDto, GraphEdgeType, GraphNodeDto, GraphNodeType, SubgraphDto } from '../../types/graph'
import { edgeVisuals } from './visuals'
import { graphLayout, type LayoutViewport } from './graphLayout'

export type UiNodeData = { dto: GraphNodeDto; dimmed: boolean; compact: boolean; highlighted: boolean; [key: string]: unknown }
export type UiEdgeData = { dto: GraphEdgeDto; highlighted: boolean; [key: string]: unknown }
export type UiNode = Node<UiNodeData, 'semantic'>
export type UiEdge = Edge<UiEdgeData, 'semantic'>

export interface GraphFilters {
  nodeTypes: ReadonlySet<GraphNodeType>
  edgeTypes: ReadonlySet<GraphEdgeType>
  status: ClaimStatus | 'ALL'
  confidence: ConfidenceTier | 'ALL'
}
export const allFilters = (): GraphFilters => ({
  nodeTypes: new Set<GraphNodeType>(), edgeTypes: new Set<GraphEdgeType>(), status: 'ALL', confidence: 'ALL',
})

export function mergeSubgraphs(current: SubgraphDto, added: SubgraphDto): SubgraphDto {
  const nodes = new Map(current.nodes.map((node) => [node.id, node]))
  const edges = new Map(current.edges.map((edge) => [edge.id, edge]))
  for (const node of added.nodes) nodes.set(node.id, node)
  for (const edge of added.edges) edges.set(edge.id, edge)
  return {
    nodes: [...nodes.values()].sort((a, b) => a.id.localeCompare(b.id)),
    edges: [...edges.values()].sort((a, b) => a.id.localeCompare(b.id)),
    truncated: current.truncated || added.truncated,
  }
}

export function filterGraph(graph: SubgraphDto, filters: GraphFilters): SubgraphDto {
  const nodes = graph.nodes.filter((node) =>
    (!filters.nodeTypes.size || filters.nodeTypes.has(node.type)) &&
    (node.type !== 'CLAIM' || (
      (filters.status === 'ALL' || node.properties.status === filters.status) &&
      (filters.confidence === 'ALL' || node.properties.confidence_tier === filters.confidence)
    )),
  )
  const ids = new Set(nodes.map((node) => node.id))
  const edges = graph.edges.filter((edge) =>
    ids.has(edge.source) && ids.has(edge.target) &&
    !filters.edgeTypes.has(edge.type),
  )
  return { nodes, edges, truncated: graph.truncated }
}

export function toFlowGraph(
  graph: SubgraphDto,
  selectedId: string | null,
  pathNodeIds: ReadonlySet<string>,
  pathEdgeIds: ReadonlySet<string>,
  compact: boolean,
  viewport?: LayoutViewport,
): { nodes: UiNode[]; edges: UiEdge[] } {
  const positions = graphLayout(graph.nodes, viewport)
  const related = new Set<string>(selectedId ? [selectedId] : [])
  if (selectedId) {
    for (const edge of graph.edges) {
      if (edge.source === selectedId) related.add(edge.target)
      if (edge.target === selectedId) related.add(edge.source)
    }
  }
  const hasPath = pathNodeIds.size > 0
  const nodes: UiNode[] = graph.nodes.map((dto) => ({
    id: dto.id, type: 'semantic', position: positions.get(dto.id) ?? { x: 0, y: 0 },
    draggable: false, selectable: true,
    data: {
      dto, compact,
      highlighted: hasPath ? pathNodeIds.has(dto.id) : dto.id === selectedId,
      dimmed: hasPath ? !pathNodeIds.has(dto.id) : Boolean(selectedId && !related.has(dto.id)),
    },
  }))
  const edges: UiEdge[] = graph.edges.map((dto) => {
    const highlighted = hasPath
      ? pathEdgeIds.has(dto.id)
      : Boolean(selectedId && (dto.source === selectedId || dto.target === selectedId))
    return {
      id: dto.id, type: 'semantic', source: dto.source, target: dto.target,
      data: { dto, highlighted },
      animated: hasPath && highlighted,
      style: { stroke: edgeVisuals[dto.type].color, opacity: hasPath && !highlighted ? 0.12 : selectedId && !highlighted ? 0.2 : 0.58 },
    }
  })
  return { nodes, edges }
}
