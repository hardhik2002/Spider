import { useMemo, useState } from 'react'
import { ArrowUpRight, BookOpenText, ChevronRight, FileSearch, Layers3, Route, ShieldAlert, X } from 'lucide-react'
import type {
  ClaimViewDto, ConsistencyDto, GraphEdgeDto, GraphNodeDto, GraphStatusDto,
  GraphSummaryDto, NodeDetailDto, SubgraphDto,
} from '../../types/graph'
import { nodeNames, statusVisuals } from '../../features/graph/visuals'
import { safeExternalUrl, shortId } from '../../utils/safety'

type Tab = 'Evidence' | 'Citations' | 'Sources' | 'Graph'
interface Props {
  open: boolean; node: GraphNodeDto | null; detail: NodeDetailDto | null
  claim: ClaimViewDto | null; graph: SubgraphDto; status: GraphStatusDto | null
  summary: GraphSummaryDto | null; consistency: ConsistencyDto | null
  onClose: () => void; onFullPassage: (id: string) => void
  onNavigate: (node: GraphNodeDto) => void; onPath: (from: string, to: string) => void
}

function ExternalLink({ value, children }: { value: string | null | undefined; children?: React.ReactNode }) {
  const safe = safeExternalUrl(value)
  return safe ? <a href={safe} target="_blank" rel="noopener noreferrer" className="source-link">{children ?? safe}<ArrowUpRight size={14} /></a> : <span className="muted-text">No public URL</span>
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return <div className="inspector-stat"><span>{label}</span><strong>{value}</strong></div>
}

function MiniNode({ node, onClick }: { node: GraphNodeDto; onClick: () => void }) {
  return <button className="mini-node" onClick={onClick}><span>{nodeNames[node.type]}</span><strong>{node.label}</strong><ChevronRight size={15} /></button>
}

function EvidenceSection({ title, tone, nodes, claim, onNavigate }: {
  title: string; tone: string; nodes: GraphNodeDto[]; claim: ClaimViewDto
  onNavigate: (node: GraphNodeDto) => void
}) {
  return <section className={`evidence-section ${tone}`}><h4>{title} <span>{nodes.length}</span></h4>
    {nodes.length ? nodes.map((node) => {
      const citations = claim.citations.filter((citation) => citation.properties.chunk_id === Number(node.id.split(':')[1]) &&
        citation.properties.relation === (tone === 'danger' ? 'CONTRADICTING' : 'SUPPORTING'))
      return <div key={node.id} className="evidence-card">
        <button onClick={() => onNavigate(node)} className="evidence-card-title"><FileSearch size={16} /><strong>{node.type === 'EVIDENCE_CHUNK' ? node.properties.source_title || node.label : node.label}</strong><ChevronRight size={15} /></button>
        {node.type === 'EVIDENCE_CHUNK' && <small>{node.properties.source_domain} · chunk {node.properties.chunk_index + 1}</small>}
        {citations.map((citation) => <blockquote key={citation.id}>“{citation.properties.exact_text}”</blockquote>)}
        {!citations.length && <span className="uncited-note">No citation shown for this relation</span>}
      </div>
    }) : <p className="empty-small">No {title.toLowerCase()} in this claim graph.</p>}
  </section>
}

export function InspectorDrawer(props: Props) {
  const [tab, setTab] = useState<Tab>('Evidence')
  const [pathTo, setPathTo] = useState('')
  const node = props.node
  const related = useMemo(() => {
    if (!node) return []
    return props.detail?.neighbors ?? props.graph.nodes.filter((item) =>
      props.graph.edges.some((edge) => (edge.source === node.id && edge.target === item.id) || (edge.target === node.id && edge.source === item.id)),
    )
  }, [node, props.detail, props.graph])
  const navigateId = (id: string) => {
    const target = [...props.graph.nodes, ...(props.claim?.nodes ?? []), ...(props.detail?.neighbors ?? [])].find((item) => item.id === id)
    if (target) props.onNavigate(target)
  }
  const docClaims = (documentId: string) => {
    const chunks = new Set(props.graph.edges.filter((edge) => edge.type === 'PART_OF_DOCUMENT' && edge.target === documentId).map((edge) => edge.source))
    return props.graph.nodes.filter((item) => item.type === 'CLAIM' && props.graph.edges.some((edge) => edge.source === item.id && chunks.has(edge.target)))
  }
  const groupRelations = (groupId: string) => props.graph.edges.filter((edge: GraphEdgeDto) =>
    edge.properties.source_group_id === groupId && ['SUPPORTED_BY', 'CONTRADICTED_BY'].includes(edge.type),
  )
  return <aside className={`inspector ${props.open ? 'is-open' : 'is-closed'}`} aria-label="Graph inspector">
    <div className="inspector-header"><div><span className="eyebrow">GRAPH INTELLIGENCE</span><strong>{node ? nodeNames[node.type] : 'Overview'}</strong></div><button aria-label="Close inspector" onClick={props.onClose}><X size={19} /></button></div>
    <div className="inspector-scroll">
      {!node && <>
        <div className="inspector-hero"><BookOpenText size={24} /><h2>Research at a glance</h2><p>Select any node to inspect its real evidence and provenance.</p></div>
        {props.summary ? <><div className="summary-grid"><Stat label="Claims" value={props.summary.claims_total} /><Stat label="Citations" value={props.summary.citations_total} /><Stat label="Documents" value={props.summary.documents_total} /><Stat label="Source groups" value={props.summary.source_groups_total} /></div>
          <section className="inspector-section"><h3>Claim landscape</h3><Stat label="Corroborated" value={props.summary.claims_corroborated} /><Stat label="Supported" value={props.summary.claims_supported} /><Stat label="Partial" value={props.summary.claims_partial} /><Stat label="Contested" value={props.summary.claims_contested} /><Stat label="Contradicted" value={props.summary.claims_contradicted} /><Stat label="Insufficient" value={props.summary.claims_insufficient} /></section></> : <p className="empty-small">No graph summary loaded.</p>}
        <section className="inspector-section"><h3>Integrity</h3><span className={`integrity-pill ${props.consistency?.status === 'VALID' ? 'valid' : 'invalid'}`}>{props.consistency?.status ?? 'NOT CHECKED'}</span>{props.consistency?.issues.length ? <p>{props.consistency.issues.length} consistency issue(s). Graph data requires review.</p> : <p>Phase 7 graph validation is based on persisted evidence.</p>}</section>
      </>}

      {node && <>
        <div className="selected-node-heading"><span className="eyebrow">SELECTED NODE · {shortId(node.id, 15)}</span><h2>{node.type === 'CLAIM' ? node.properties.claim_text : node.type === 'CITATION' ? 'Exact citation' : node.label}</h2>
          {node.type === 'CLAIM' && <div className="badge-row"><span className={`status-badge ${statusVisuals[node.properties.status].className}`}>{statusVisuals[node.properties.status].label}</span><span className="confidence-badge">{node.properties.confidence_tier}</span></div>}
        </div>
        {node.type === 'CLAIM' && <>
          <div className="summary-grid"><Stat label="Support" value={node.properties.support_count} /><Stat label="Contradiction" value={node.properties.contradiction_count} /><Stat label="Source groups" value={node.properties.independent_support_groups} /><Stat label="Claim type" value={node.properties.claim_type} /></div>
          <div className="inspector-tabs" role="tablist">{(['Evidence', 'Citations', 'Sources', 'Graph'] as Tab[]).map((item) => <button key={item} role="tab" aria-selected={tab === item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)}>{item}</button>)}</div>
          {!props.claim ? <p className="empty-small">Loading claim evidence…</p> : <>
            {tab === 'Evidence' && <><EvidenceSection title="Supporting" tone="good" nodes={props.claim.supporting_chunks} claim={props.claim} onNavigate={props.onNavigate} /><EvidenceSection title="Partial support" tone="warn" nodes={props.claim.partial_support_chunks} claim={props.claim} onNavigate={props.onNavigate} /><EvidenceSection title="Contradicting" tone="danger" nodes={props.claim.contradicting_chunks} claim={props.claim} onNavigate={props.onNavigate} /></>}
            {tab === 'Citations' && <section className="inspector-section"><h3>Exact citations <span>{props.claim.citations.length}</span></h3>{props.claim.citations.length ? props.claim.citations.map((citation) => <button className="citation-card" key={citation.id} onClick={() => props.onNavigate(citation)}><span>{citation.properties.relation}</span><blockquote>“{citation.properties.exact_text}”</blockquote><small>Offsets {citation.properties.start_offset}–{citation.properties.end_offset}</small></button>) : <p className="empty-small">No citations for this claim.</p>}</section>}
            {tab === 'Sources' && <section className="inspector-section"><h3>Source groups</h3>{props.claim.source_groups.length ? props.claim.source_groups.map((group) => <MiniNode key={group.id} node={group} onClick={() => props.onNavigate(group)} />) : <p className="empty-small">No source groups linked.</p>}<h3>Documents</h3>{props.claim.documents.map((document) => <MiniNode key={document.id} node={document} onClick={() => props.onNavigate(document)} />)}</section>}
            {tab === 'Graph' && <section className="inspector-section"><h3>Provenance</h3><Stat label="Origin passages" value={props.claim.connectivity.origin_count ?? 0} /><Stat label="Connected documents" value={props.claim.connectivity.document_count ?? 0} /><button className="secondary-button" onClick={() => props.onPath(`research:${node.properties.research_job_id}`, node.id)}><Route size={15} /> Trace from research</button></section>}
          </>}
        </>}
        {node.type === 'CITATION' && <section className="inspector-section"><span className={`relation-tag ${node.properties.relation === 'CONTRADICTING' ? 'danger' : 'good'}`}>{node.properties.relation}</span><blockquote className="citation-full">“{node.properties.exact_text}”</blockquote><Stat label="Start offset" value={node.properties.start_offset} /><Stat label="End offset" value={node.properties.end_offset} /><Stat label="Span valid" value={node.properties.valid ? 'Yes' : 'No'} /><ExternalLink value={node.properties.source_url}>Open source</ExternalLink><button className="secondary-button" onClick={() => navigateId(`claim:${node.properties.claim_id}`)}>View claim</button><button className="secondary-button" onClick={() => navigateId(`evidence_chunk:${node.properties.chunk_id}`)}>View passage</button></section>}
        {node.type === 'EVIDENCE_CHUNK' && <section className="inspector-section"><h3>Evidence passage</h3><Stat label="Source" value={node.properties.source_title ?? 'Untitled'} /><Stat label="Domain" value={node.properties.source_domain} /><Stat label="Chunk index" value={node.properties.chunk_index} /><ExternalLink value={node.properties.source_url}>Open source</ExternalLink>{props.detail?.node.text ? <p className="passage-text">{props.detail.node.text}</p> : <button className="secondary-button" onClick={() => props.onFullPassage(node.id)}><FileSearch size={15} /> View full passage</button>}</section>}
        {node.type === 'DOCUMENT' && <section className="inspector-section"><h3>Document</h3><Stat label="Domain" value={node.properties.source_domain} /><Stat label="Content hash" value={shortId(node.properties.content_hash, 16)} /><ExternalLink value={node.properties.source_url}>Open document</ExternalLink><h3>Related claims in loaded graph</h3>{docClaims(node.id).map((item) => <MiniNode key={item.id} node={item} onClick={() => props.onNavigate(item)} />)}<h3>Related citations in loaded graph</h3>{props.graph.nodes.filter((item) => item.type === 'CITATION' && item.properties.document_id === node.properties.document_id).map((item) => <MiniNode key={item.id} node={item} onClick={() => props.onNavigate(item)} />)}</section>}
        {node.type === 'SOURCE_GROUP' && <section className="inspector-section"><Layers3 size={20} /><h3>Independent source group</h3><Stat label="Group ID" value={shortId(node.properties.source_group_id, 18)} /><Stat label="Member documents" value={node.properties.member_count} /><Stat label="Source type" value={node.properties.source_type} /><Stat label="Claims supported" value={props.summary?.source_group_contribution[node.properties.source_group_id]?.claims_supported ?? '—'} /><Stat label="Claims contradicted" value={props.summary?.source_group_contribution[node.properties.source_group_id]?.claims_contradicted ?? '—'} /><Stat label="Citations provided" value={props.summary?.source_group_contribution[node.properties.source_group_id]?.citations_provided ?? '—'} /><h3>Connected claims in loaded graph</h3>{groupRelations(node.properties.source_group_id).map((edge) => {
          const item = props.graph.nodes.find((candidate) => candidate.id === edge.source)
          return item ? <MiniNode key={edge.id} node={item} onClick={() => props.onNavigate(item)} /> : null
        })}</section>}
        {node.type === 'RESEARCH' && <section className="inspector-section"><h3>Research question</h3><p>{node.properties.question}</p><Stat label="Research status" value={node.properties.status} /><Stat label="Graph status" value={props.status?.status ?? 'Unknown'} /><Stat label="Snapshot" value={shortId(props.status?.graph_snapshot_id ?? '', 16)} /><Stat label="Validation" value={props.status?.validation_status ?? 'Unknown'} /></section>}
        {node.type === 'SUBQUESTION' && <section className="inspector-section"><h3>Subquestion</h3><p>{node.properties.question}</p><Stat label="Priority" value={node.properties.priority} /><Stat label="Status" value={node.properties.status} /><h3>Associated claims in loaded graph</h3>{related.filter((item) => item.type === 'CLAIM').map((item) => <MiniNode key={item.id} node={item} onClick={() => props.onNavigate(item)} />)}</section>}
        <section className="inspector-section related-section"><h3>Connected nodes <span>{related.length}</span></h3>{related.slice(0, 12).map((item) => <MiniNode key={item.id} node={item} onClick={() => props.onNavigate(item)} />)}{related.length > 12 && <p className="empty-small">Showing 12 nearby nodes.</p>}</section>
        <section className="inspector-section"><h3><Route size={16} /> Directed path</h3><p className="small-copy">Trace from this node to another loaded node ID.</p><form className="path-form" onSubmit={(event) => { event.preventDefault(); if (pathTo.trim()) props.onPath(node.id, pathTo.trim()) }}><input aria-label="Path destination node ID" placeholder="claim:… or citation:…" value={pathTo} onChange={(event) => setPathTo(event.target.value)} /><button type="submit" aria-label="Trace path"><ChevronRight size={17} /></button></form></section>
      </>}
    </div>
    <div className="inspector-bottom"><ShieldAlert size={15} /><span>Evidence strength is not truth probability.</span></div>
  </aside>
}
