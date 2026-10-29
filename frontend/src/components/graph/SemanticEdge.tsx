import { memo } from 'react'
import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from '@xyflow/react'
import type { UiEdge } from '../../features/graph/graphAdapter'
import { edgeVisuals } from '../../features/graph/visuals'

export const SemanticEdge = memo(function SemanticEdge(props: EdgeProps<UiEdge>) {
  const [path, labelX, labelY] = getBezierPath(props)
  const type = props.data?.dto.type ?? 'HAS_CLAIM'
  const visual = edgeVisuals[type]
  const highlighted = Boolean(props.data?.highlighted || props.selected)
  return <>
    <BaseEdge id={props.id} path={path} style={{
      stroke: visual.color, strokeWidth: highlighted ? 2.8 : 1.35,
      strokeDasharray: visual.dash, opacity: highlighted ? 1 : props.style?.opacity,
      filter: highlighted ? `drop-shadow(0 0 5px ${visual.color})` : undefined,
    }} />
    {highlighted && <EdgeLabelRenderer><span className="edge-label" style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}>{visual.label}</span></EdgeLabelRenderer>}
  </>
})
