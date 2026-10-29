import { useEffect, useRef, useState } from 'react'
import { ArrowRight, AlertTriangle, GitBranch, LoaderCircle, X } from 'lucide-react'
import { useSpiderGraph } from '../hooks/useSpiderGraph'
import { allFilters, filterGraph, type GraphFilters } from '../features/graph/graphAdapter'
import type { GraphNodeDto } from '../types/graph'
import { TopHeader } from '../components/layout/TopHeader'
import { Sidebar } from '../components/layout/Sidebar'
import { GraphWorkspace } from '../components/graph/GraphWorkspace'
import { GraphLoader } from '../components/research/GraphLoader'
import { InspectorDrawer } from '../components/inspector/InspectorDrawer'

export function App() {
  const spider = useSpiderGraph()
  const [filters, setFilters] = useState<GraphFilters>(allFilters)
  const [loaderOpen, setLoaderOpen] = useState(true)
  const [inspectorOpen, setInspectorOpen] = useState(() => window.innerWidth > 760)
  const [contradictionsOpen, setContradictionsOpen] = useState(false)
  const [focusId, setFocusId] = useState<string | null>(null)
  const [fitSignal, setFitSignal] = useState(0)
  const started = useRef(false)
  useEffect(() => {
    if (started.current) return
    started.current = true
    if (spider.ids.researchId && spider.ids.snapshotId) void spider.load(spider.ids.researchId, spider.ids.snapshotId)
  }, [spider])
  useEffect(() => { if (spider.phase === 'ready') setLoaderOpen(false) }, [spider.phase])

  const select = (node: GraphNodeDto) => {
    if (!filterGraph(spider.graph, filters).nodes.some((item) => item.id === node.id)) setFilters(allFilters())
    setInspectorOpen(true)
    setFocusId(node.id)
    void spider.select(node)
  }
  const showContradictions = () => {
    setContradictionsOpen(true)
    void spider.loadContradictions()
  }
  const pathNodeIds = new Set(spider.path?.nodes.map((node) => node.id) ?? [])
  const pathEdgeIds = new Set(spider.path?.edges.map((edge) => edge.id) ?? [])
  const loaded = spider.phase === 'ready'

  return <div className="app-shell">
    <TopHeader nodes={spider.graph.nodes} onFocus={select} onRefresh={() => { void spider.load(spider.ids.researchId, spider.ids.snapshotId) }} onFit={() => setFitSignal((value) => value + 1)} loaded={loaded} consistency={spider.consistency?.status ?? null} />
    <div className="app-main">
      <Sidebar filters={filters} onFilters={(next) => { setFilters(next); spider.clearSelection(); spider.clearPath() }} status={spider.status} summary={spider.summary} onContradictions={showContradictions} onLoadAnother={() => setLoaderOpen(true)} />
      <main className="canvas-column">
        {spider.consistency?.status === 'INVALID' && <div className="consistency-warning" role="alert"><AlertTriangle size={16} /> Graph consistency is invalid. Inspect the backend before relying on this view.</div>}
        {spider.error && !loaderOpen && <div className="canvas-error" role="alert"><AlertTriangle size={16} /> {spider.error}<button aria-label="Dismiss error" onClick={spider.clearError}><X size={15} /></button></div>}
        <GraphWorkspace graph={spider.graph} filters={filters} selectedId={spider.selectedId} pathNodeIds={pathNodeIds} pathEdgeIds={pathEdgeIds} focusId={focusId} fitSignal={fitSignal} onSelect={select} onExpand={(id) => { void spider.expand(id).then(() => setFitSignal((value) => value + 1)) }} onClear={spider.clearSelection} expandingId={spider.expandingId} />
        {!loaderOpen && (spider.phase === 'loading' || spider.phase === 'building') && <div className="canvas-state" role="status"><LoaderCircle className="spin" size={23} /><strong>{spider.phase === 'building' ? 'Building graph…' : 'Loading graph…'}</strong><span>Checking the snapshot and loading its bounded research neighborhood.</span></div>}
        {spider.path && <button className="path-clear" onClick={spider.clearPath}><GitBranch size={15} /> Path highlighted · clear <X size={14} /></button>}
        {!loaded && !loaderOpen && <div className="canvas-empty"><h2>No graph loaded</h2><p>Connect to a validated Phase 7 graph to start exploring.</p><button className="primary-button" onClick={() => setLoaderOpen(true)}>Load graph <ArrowRight size={16} /></button></div>}
      </main>
      <InspectorDrawer open={inspectorOpen} node={spider.selectedNode} detail={spider.detail} claim={spider.claimView} graph={spider.graph} status={spider.status} summary={spider.summary} consistency={spider.consistency} onClose={() => setInspectorOpen(false)} onFullPassage={(id) => void spider.viewFullPassage(id)} onNavigate={select} onPath={(from, to) => void spider.focusPath(from, to)} />
    </div>
    {contradictionsOpen && <div className="overlay-scrim" onClick={() => setContradictionsOpen(false)}><aside className="contradictions-drawer" onClick={(event) => event.stopPropagation()} aria-label="Contradictory claims"><div className="drawer-title"><div><span className="eyebrow">EVIDENCE CONFLICTS</span><h2>Contradictions</h2></div><button aria-label="Close contradictions" onClick={() => setContradictionsOpen(false)}><X size={19} /></button></div>{spider.contradictions ? spider.contradictions.length ? spider.contradictions.map((claim) => <button key={claim.claim.id} className="conflict-item" onClick={() => { void spider.focusClaim(claim); setContradictionsOpen(false); setInspectorOpen(true); setFocusId(claim.claim.id) }}><span>{claim.status}</span><strong>{claim.claim.properties.claim_text}</strong><small>{claim.supporting_chunks.length} supporting · {claim.contradicting_chunks.length} contradicting passages</small></button>) : <p className="empty-small">No contested or contradicted claims in this snapshot.</p> : <p className="empty-small">Loading contradictions…</p>}</aside></div>}
    {loaderOpen && <GraphLoader key={`${spider.ids.researchId}:${spider.ids.snapshotId}`} initialResearchId={spider.ids.researchId ?? ''} initialSnapshotId={spider.ids.snapshotId ?? ''} busy={spider.phase === 'loading' || spider.phase === 'building'} error={spider.phase === 'error' ? spider.error : null} onLoad={(researchId, snapshotId) => void spider.load(researchId, snapshotId)} onBuild={(researchId, evidenceId) => void spider.build(researchId, evidenceId)} onClose={loaded ? () => setLoaderOpen(false) : undefined} />}
  </div>
}
