import type { XYPosition } from '@xyflow/react'
import type { GraphNodeDto, GraphNodeType } from '../../types/graph'

export interface LayoutViewport { width: number; height: number; zoom: number }

export function spiderSizeForWidth(width: number): number {
  if (width >= 1000) return 480
  if (width >= 780) return 400
  if (width >= 710) return 360
  return 340
}

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
export function graphLayout(nodes: GraphNodeDto[], viewport?: LayoutViewport): Map<string, XYPosition> {
  const grouped = new Map<GraphNodeType, GraphNodeDto[]>()
  for (const node of nodes) grouped.set(node.type, [...(grouped.get(node.type) ?? []), node])
  const positions = new Map<string, XYPosition>()
  const dynamic = viewport && viewport.width > 0 && viewport.height > 0
  const zoom = dynamic ? viewport.zoom : 1
  const cardWidth = 224 * zoom
  const spiderSize = dynamic ? spiderSizeForWidth(viewport.width) : 0
  const sidePadding = dynamic
    ? Math.max(12, (viewport.width - spiderSize) / 2 - cardWidth - 16)
    : 0
  const sideX = dynamic ? {
    left: (sidePadding - viewport.width / 2) / zoom,
    right: (viewport.width / 2 - sidePadding - cardWidth) / zoom,
  } : null
  const rowY = dynamic ? {
    top: (55 - viewport.height / 2) / zoom,
    upper: (viewport.height * 0.21 - viewport.height / 2) / zoom,
    middle: (viewport.height * 0.47 - viewport.height / 2) / zoom,
    lower: (viewport.height * 0.72 - viewport.height / 2) / zoom,
  } : null
  for (const [type, items] of grouped) {
    items.sort((a, b) => a.id.localeCompare(b.id))
    const sector = sectors[type]
    items.forEach((node, index) => {
      let x = sector.x
      let y = sector.y
      if (sideX && rowY) {
        x = type === 'RESEARCH' ? -112 :
          ['SUBQUESTION', 'SOURCE_GROUP', 'DOCUMENT'].includes(type) ? sideX.left : sideX.right
        y = type === 'RESEARCH' ? rowY.top :
          ['SUBQUESTION', 'CLAIM'].includes(type) ? rowY.upper :
          ['SOURCE_GROUP', 'EVIDENCE_CHUNK'].includes(type) ? rowY.middle : rowY.lower
      }
      positions.set(node.id, {
        x,
        y: y + index * (type === 'RESEARCH' ? 0 : 132),
      })
    })
  }
  return positions
}
