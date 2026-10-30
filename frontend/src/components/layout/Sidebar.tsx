import { useState } from 'react'
import { Activity, BookOpen, ChevronDown, CircleHelp, Database, FileSearch, FileText, Info, Layers3, Link2, Menu, Network, Quote, Settings2, ShieldAlert, X } from 'lucide-react'
import type { ClaimStatus, ConfidenceTier, GraphEdgeType, GraphNodeType, GraphStatusDto, GraphSummaryDto } from '../../types/graph'
import type { GraphFilters } from '../../features/graph/graphAdapter'
import { edgeVisuals } from '../../features/graph/visuals'

interface Props {
  filters: GraphFilters; onFilters: (filters: GraphFilters) => void
  status: GraphStatusDto | null; summary: GraphSummaryDto | null
  onContradictions: () => void; onLoadAnother: () => void
}
const nav: { label: string; type: GraphNodeType | null; icon: typeof Network }[] = [
  { label: 'Explore', type: null, icon: Network },
  { label: 'Research', type: 'RESEARCH', icon: BookOpen },
  { label: 'Claims', type: 'CLAIM', icon: Quote },
  { label: 'Evidence', type: 'EVIDENCE_CHUNK', icon: FileSearch },
  { label: 'Documents', type: 'DOCUMENT', icon: FileText },
  { label: 'Source Groups', type: 'SOURCE_GROUP', icon: Layers3 },
  { label: 'Citations', type: 'CITATION', icon: Link2 },
  { label: 'Subquestions', type: 'SUBQUESTION', icon: CircleHelp },
]

export function Sidebar({ filters, onFilters, status, summary, onContradictions, onLoadAnother }: Props) {
  const [open, setOpen] = useState(false)
  const [advanced, setAdvanced] = useState(false)
  const [about, setAbout] = useState(false)
  const active = filters.nodeTypes.size === 1 ? [...filters.nodeTypes][0] : null
  const chooseType = (type: GraphNodeType | null) => { onFilters({ ...filters, nodeTypes: type ? new Set([type]) : new Set() }); setOpen(false) }
  return <>
    <button className="mobile-menu" aria-label={open ? 'Close navigation' : 'Open navigation'} onClick={() => setOpen(!open)}>{open ? <X size={19} /> : <Menu size={19} />}</button>
    {open && <button className="mobile-scrim" aria-label="Close navigation" onClick={() => setOpen(false)} />}
    <aside className={`sidebar ${open ? 'is-open' : ''}`}>
      <div className="sidebar-scroll">
        <p className="sidebar-caption">WORKSPACE</p>
        <nav aria-label="Graph sections">
          {nav.map(({ label, type, icon: Icon }) => <button key={label} className={`nav-item ${active === type ? 'active' : ''}`} onClick={() => chooseType(type)}><Icon size={17} /><span>{label}</span>{label === 'Claims' && summary && <em>{summary.claims_total}</em>}</button>)}
        </nav>
        <div className="sidebar-divider" />
        <p className="sidebar-caption">INVESTIGATE</p>
        <button className="nav-item" onClick={onContradictions}><ShieldAlert size={17} /><span>Contradictions</span>{summary && <em>{summary.contradiction_edges}</em>}</button>
        <button className="nav-item" onClick={onLoadAnother}><Database size={17} /><span>Jobs</span></button>
        <button className="nav-item" onClick={() => setAdvanced(!advanced)} aria-expanded={advanced}><Settings2 size={17} /><span>Settings & filters</span><ChevronDown size={15} className={advanced ? 'rotate' : ''} /></button>
        {advanced && <div className="sidebar-filters">
          <label>Claim status<select aria-label="Filter claim status" value={filters.status} onChange={(event) => onFilters({ ...filters, status: event.target.value as ClaimStatus | 'ALL' })}>
            {['ALL', 'CORROBORATED', 'SUPPORTED', 'PARTIALLY_SUPPORTED', 'CONTESTED', 'CONTRADICTED', 'INSUFFICIENT_EVIDENCE'].map((item) => <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>)}
          </select></label>
          <label>Confidence<select aria-label="Filter confidence" value={filters.confidence} onChange={(event) => onFilters({ ...filters, confidence: event.target.value as ConfidenceTier | 'ALL' })}>
            {['ALL', 'HIGH', 'MEDIUM', 'LOW', 'UNRESOLVED'].map((item) => <option key={item}>{item}</option>)}
          </select></label>
          <span>Visible edge types</span>
          {(Object.keys(edgeVisuals) as GraphEdgeType[]).map((type) => <label className="filter-check" key={type}><input type="checkbox" checked={!filters.edgeTypes.has(type)} onChange={() => {
            const hidden = new Set(filters.edgeTypes)
            if (hidden.has(type)) hidden.delete(type); else hidden.add(type)
            onFilters({ ...filters, edgeTypes: hidden })
          }} />{edgeVisuals[type].label}</label>)}
        </div>}
        <button className="nav-item" onClick={() => setAbout(!about)} aria-expanded={about}><Info size={17} /><span>About</span><ChevronDown size={15} className={about ? 'rotate' : ''} /></button>
        {about && <div className="sidebar-about">SpiderMind Phase 8A visualizes persisted Phase 7 evidence. It makes no new claim or confidence decision.</div>}
      </div>
      <div className="sidebar-context">
        <span className="sidebar-caption">ACTIVE RESEARCH</span>
        {status ? <><strong><span className="live-dot" />{status.status}</strong><p>Research graph</p><div className="context-grid"><span>Nodes <b>{status.node_count}</b></span><span>Edges <b>{status.edge_count}</b></span></div><small>Graph {status.validation_status}</small></> : <p>No graph loaded</p>}
        <button className="context-switch" onClick={onLoadAnother}><Database size={14} />{status ? 'Research home' : 'Research home'}</button>
      </div>
      <div className="sidebar-footer"><Activity size={14} /> PHASE 8A · GRAPH CLIENT</div>
    </aside>
  </>
}
