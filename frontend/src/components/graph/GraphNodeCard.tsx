import { memo } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { BookOpenText, CircleHelp, FileSearch, FileText, Layers3, Link2, Quote, Target } from 'lucide-react'
import type { UiNode } from '../../features/graph/graphAdapter'
import { nodeNames, statusVisuals } from '../../features/graph/visuals'
import { shortId } from '../../utils/safety'

const icons = {
  RESEARCH: Target, SUBQUESTION: CircleHelp, CLAIM: Quote, CITATION: Link2,
  EVIDENCE_CHUNK: FileSearch, DOCUMENT: FileText, SOURCE_GROUP: Layers3,
}

export const GraphNodeCard = memo(function GraphNodeCard({ data }: NodeProps<UiNode>) {
  const { dto, compact, dimmed, highlighted } = data
  const Icon = icons[dto.type] ?? BookOpenText
  let subtitle = ''
  let badge = ''
  if (dto.type === 'CLAIM') {
    subtitle = dto.properties.confidence_tier
    badge = statusVisuals[dto.properties.status].label
  } else if (dto.type === 'DOCUMENT') subtitle = dto.properties.source_domain
  else if (dto.type === 'EVIDENCE_CHUNK') subtitle = dto.properties.heading || dto.properties.source_domain
  else if (dto.type === 'CITATION') subtitle = dto.properties.relation
  else if (dto.type === 'SOURCE_GROUP') subtitle = `${dto.properties.member_count} document${dto.properties.member_count === 1 ? '' : 's'}`
  else if (dto.type === 'SUBQUESTION') subtitle = `Priority ${dto.properties.priority}`
  else subtitle = 'Research question'
  const title = dto.type === 'CITATION' ? dto.properties.exact_text : dto.label
  return (
    <div className={`graph-node type-${dto.type.toLowerCase()} ${highlighted ? 'is-highlighted' : ''} ${dimmed ? 'is-dimmed' : ''} ${compact ? 'is-compact' : ''}`} title={title}>
      <Handle type="target" position={Position.Left} className="invisible-handle" />
      <div className="graph-node-top">
        <span className="graph-node-icon"><Icon size={16} strokeWidth={1.8} /></span>
        <span className="graph-node-kind">{nodeNames[dto.type]}</span>
        {dto.type === 'CLAIM' && <span className={`graph-node-status ${statusVisuals[dto.properties.status].className}`} />}
      </div>
      <strong className="graph-node-title">{compact ? title.slice(0, 34) : title}</strong>
      {!compact && <div className="graph-node-bottom"><span>{subtitle}</span><span>{shortId(dto.id, 5)}</span></div>}
      {!compact && badge && <span className={`graph-node-badge ${dto.type === 'CLAIM' ? statusVisuals[dto.properties.status].className : ''}`}>{badge}</span>}
      <Handle type="source" position={Position.Right} className="invisible-handle" />
    </div>
  )
})
