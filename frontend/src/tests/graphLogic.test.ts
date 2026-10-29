import { describe, expect, it } from 'vitest'
import { GRAPH_EDGE_TYPES, GRAPH_NODE_TYPES, type GraphNodeDto } from '../types/graph'
import { allFilters, filterGraph, mergeSubgraphs, toFlowGraph } from '../features/graph/graphAdapter'
import { graphLayout } from '../features/graph/graphLayout'
import { edgeVisuals, nodeNames, statusVisuals } from '../features/graph/visuals'
import { claim, graph, initialGraph } from './fixture'

describe('Phase 7 graph adapter', () => {
  it('maps every node and edge type without changing semantics', () => {
    expect(Object.keys(nodeNames).sort()).toEqual([...GRAPH_NODE_TYPES].sort())
    expect(Object.keys(edgeVisuals).sort()).toEqual([...GRAPH_EDGE_TYPES].sort())
    expect(edgeVisuals.CONTRADICTED_BY.color).not.toBe(edgeVisuals.SUPPORTED_BY.color)
    expect(statusVisuals.CONTESTED.label).toBe('Contested')
    expect(statusVisuals.INSUFFICIENT_EVIDENCE.label).toBe('Insufficient evidence')
  })

  it('merges progressive neighborhoods and keeps parallel semantic edges', () => {
    const merged = mergeSubgraphs(initialGraph, graph)
    expect(merged.nodes).toHaveLength(graph.nodes.length)
    expect(merged.edges).toHaveLength(graph.edges.length)
    expect(merged.edges.filter((edge) => edge.source === claim.id && edge.target === 'evidence_chunk:1')).toHaveLength(3)
  })

  it('filters claim status, confidence, node type, and hidden edge types', () => {
    const contested = filterGraph(graph, { ...allFilters(), nodeTypes: new Set(['CLAIM']), status: 'CONTESTED' })
    expect(contested.nodes.map((node) => node.id)).toEqual([claim.id])
    expect(contested.edges).toHaveLength(0)
    expect(filterGraph(graph, { ...allFilters(), confidence: 'HIGH' }).nodes.some((node) => node.id === claim.id)).toBe(false)
    const noOrigins = filterGraph(graph, { ...allFilters(), edgeTypes: new Set(['ORIGINATED_FROM']) })
    expect(noOrigins.edges.some((edge) => edge.type === 'ORIGINATED_FROM')).toBe(false)
    expect(noOrigins.edges.some((edge) => edge.type === 'SUPPORTED_BY')).toBe(true)
  })

  it('highlights selected neighbors and directed path while dimming unrelated nodes', () => {
    const selected = toFlowGraph(graph, claim.id, new Set(), new Set(), false)
    expect(selected.nodes.find((node) => node.id === claim.id)?.data.highlighted).toBe(true)
    expect(selected.nodes.find((node) => node.id === 'source_group:group-1')?.data.dimmed).toBe(true)
    const path = toFlowGraph(graph, null, new Set([claim.id, 'citation:1']), new Set(['e7']), false)
    expect(path.edges.find((edge) => edge.id === 'e7')?.animated).toBe(true)
    expect(path.edges.find((edge) => edge.id === 'e6')?.data?.highlighted).toBe(false)
  })

  it('lays out 25, 100, and 250 nodes deterministically without card collisions', () => {
    for (const count of [25, 100, 250]) {
      const nodes: GraphNodeDto[] = Array.from({ length: count }, (_, index) => ({
        ...claim, id: `claim:test-${index}`,
      }))
      const started = performance.now()
      const first = graphLayout(nodes)
      const elapsed = performance.now() - started
      const second = graphLayout([...nodes].reverse())
      expect([...first]).toEqual([...second])
      expect(new Set([...first.values()].map((point) => `${point.x}:${point.y}`)).size).toBe(count)
      expect(elapsed).toBeLessThan(2000)
    }
  })
})
