import type { XYPosition } from '@xyflow/react'
import type { GraphNodeDto, GraphNodeType } from '../../types/graph'

const sectors: Record<GraphNodeType, { x: number; y: number }> = {
  RESEARCH: { x: -112, y: -415 },
  SUBQUESTION: { x: -750, y: -330 },
  CLAIM: { x: 445, y: -350 },
  EVIDENCE_CHUNK: { x: 750, y: -170 },
  CITATION: { x: 180, y: 470 },
  DOCUMENT: { x: -400, y: 490 },
  SOURCE_GROUP: { x: -1060, y: 90 },
}

/** Stable spiderweb sectors, spaced to keep full-size cards from colliding. */
export function graphLayout(nodes: GraphNodeDto[]): Map<string, XYPosition> {
  const grouped = new Map<GraphNodeType, GraphNodeDto[]>()
  for (const node of nodes) grouped.set(node.type, [...(grouped.get(node.type) ?? []), node])
  const positions = new Map<string, XYPosition>()
  for (const [type, items] of grouped) {
    items.sort((a, b) => a.id.localeCompare(b.id))
    const sector = sectors[type]
    items.forEach((node, index) => {
      positions.set(node.id, {
        x: sector.x,
        y: sector.y + index * (type === 'RESEARCH' ? 0 : 132),
      })
    })
  }
  return positions
}
