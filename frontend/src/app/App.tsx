import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowRight, AlertTriangle, GitBranch, LoaderCircle, X } from 'lucide-react'
import { useSpiderGraph } from '../hooks/useSpiderGraph'
import { allFilters, filterGraph, type GraphFilters } from '../features/graph/graphAdapter'
import type { GraphNodeDto } from '../types/graph'
import { TopHeader } from '../components/layout/TopHeader'
import { Sidebar } from '../components/layout/Sidebar'
import { GraphWorkspace } from '../components/graph/GraphWorkspace'
import { GraphLoader } from '../components/research/GraphLoader'
import { InspectorDrawer } from '../components/inspector/InspectorDrawer'
import { ResearchHome } from '../components/research/ResearchHome'
import { ResearchRunView } from '../components/research/ResearchRunView'
import { useResearchRun } from '../features/research/useResearchRun'

function routeNow() { return window.location.pathname }

export function App() {
  const [path, setPath] = useState(routeNow)
  const navigate = useCallback((next: string) => { window.history.pushState(null, '', next); setPath(next) }, [])
  useEffect(() => { const update = () => setPath(routeNow()); window.addEventListener('popstate', update); return () => window.removeEventListener('popstate', update) }, [])
  const research = useResearchRun(navigate)
  useEffect(() => { if (research.run && !['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED'].includes(research.run.stage)) research.resume() }, [])
  if (path === '/developer/open-graph') return <GraphApp developer onHome={() => navigate('/')} />
  if (/^\/research\/[^/]+\/graph\/[^/]+$/.test(path)) return <GraphApp developer={false} onHome={() => navigate('/')} />
  const progressMatch = path.match(/^\/research\/([^/]+)$/)
  if (progressMatch) return <ResearchRunView run={research.run?.researchJobId === decodeURIComponent(progressMatch[1]) ? research.run : null} onRetry={research.retry} onStop={() => void research.stop()} onReset={research.reset} onExplore={() => research.run && navigate(`/research/${encodeURIComponent(research.run.researchJobId!)}/graph/${encodeURIComponent(research.run.graphSnapshotId!)}`)} />
  return <ResearchHome run={research.run} onStart={research.start} onResume={() => { research.resume(); if (research.run?.researchJobId) navigate(`/research/${encodeURIComponent(research.run.researchJobId)}`) }} onDeveloper={() => navigate('/developer/open-graph')} />
}

function GraphApp({ developer, onHome }: { developer: boolean; onHome: () => void }) {
  const spider = useSpiderGraph()
  const [filters, setFilters] = useState<GraphFilters>(allFilters)
  const [loaderOpen, setLoaderOpen] = useState(developer)
  const [inspectorOpen, setInspectorOpen] = useState(() => window.innerWidth > 760)
  const [contradictionsOpen, setContradictionsOpen] = useState(false)
  const [focusId, setFocusId] = useState<string | null>(null)
  const [fitSignal, setFitSignal] = useState(0)
  const started = useRef(false)
  useEffect(() => {
    if (started.current) return
    started.current = true
    if (window.location.pathname.match(/^\/research\/[^/]+\/graph\/[^/]+$/) && spider.ids.researchId && spider.ids.snapshotId) void spider.load(spider.ids.researchId, spider.ids.snapshotId)
  }, [spider.ids.researchId, spider.ids.snapshotId, spider.load])
  useEffect(() => { if (spider.phase === 'ready') setLoaderOpen(false) }, [spider.phase])

  const select = useCallback((node: GraphNodeDto, focus = true) => {
    if (!filterGraph(spider.graph, filters).nodes.some((item) => item.id === node.id)) setFilters(allFilters())
    setInspectorOpen(true)
    setFocusId(focus ? node.id : null)
    void spider.select(node)
  }, [spider.graph, spider.select, filters])
  const selectFromCanvas = useCallback((node: GraphNodeDto) => select(node, false), [select])
  const showContradictions = () => {
    setContradictionsOpen(true)
    void spider.loadContradictions()
  }
  const pathNodeIds = useMemo(() => new Set(spider.path?.nodes.map((node) => node.id) ?? []), [spider.path])
  const pathEdgeIds = useMemo(() => new Set(spider.path?.edges.map((edge) => edge.id) ?? []), [spider.path])
  const loaded = spider.phase === 'ready'

  return <div className="app-shell">
    <TopHeader nodes={spider.graph.nodes} onFocus={select} onRefresh={() => { void spider.load(spider.ids.researchId, spider.ids.snapshotId) }} onFit={() => setFitSignal((value) => value + 1)} loaded={loaded} consistency={spider.consistency?.status ?? null} />
    <div className="app-main">
      <Sidebar filters={filters} onFilters={(next) => { setFilters(next); spider.clearSelection(); spider.clearPath() }} status={spider.status} summary={spider.summary} onContradictions={showContradictions} onLoadAnother={developer ? () => setLoaderOpen(true) : onHome} />
      <main className="canvas-column">
        {spider.consistency?.status === 'INVALID' && <div className="consistency-warning" role="alert"><AlertTriangle size={16} /> Graph consistency is invalid. Inspect the backend before relying on this view.</div>}
        {spider.error && !loaderOpen && <div className="canvas-error" role="alert"><AlertTriangle size={16} /> {spider.error}<button aria-label="Dismiss error" onClick={spider.clearError}><X size={15} /></button></div>}
        <GraphWorkspace graph={spider.graph} filters={filters} selectedId={spider.selectedId} pathNodeIds={pathNodeIds} pathEdgeIds={pathEdgeIds} focusId={focusId} fitSignal={fitSignal} onSelect={selectFromCanvas} onExpand={(id) => { void spider.expand(id) }} onClear={spider.clearSelection} expandingId={spider.expandingId} />
        {!loaderOpen && (spider.phase === 'loading' || spider.phase === 'building') && <div className="canvas-state" role="status"><LoaderCircle className="spin" size={23} /><strong>{spider.phase === 'building' ? 'Building graph…' : 'Loading graph…'}</strong><span>Checking the snapshot and loading its bounded research neighborhood.</span></div>}
        {spider.path && <button className="path-clear" onClick={spider.clearPath}><GitBranch size={15} /> Path highlighted · clear <X size={14} /></button>}
        {!loaded && !loaderOpen && <div className="canvas-empty"><h2>No graph loaded</h2><p>Return to research or open Developer Tools to inspect a snapshot.</p><button className="primary-button" onClick={onHome}>Research home <ArrowRight size={16} /></button></div>}
      </main>
      <InspectorDrawer open={inspectorOpen} node={spider.selectedNode} detail={spider.detail} claim={spider.claimView} graph={spider.graph} status={spider.status} summary={spider.summary} consistency={spider.consistency} onClose={() => setInspectorOpen(false)} onFullPassage={(id) => void spider.viewFullPassage(id)} onNavigate={select} onPath={(from, to) => void spider.focusPath(from, to)} />
    </div>
    {contradictionsOpen && <div className="overlay-scrim" onClick={() => setContradictionsOpen(false)}><aside className="contradictions-drawer" onClick={(event) => event.stopPropagation()} aria-label="Contradictory claims"><div className="drawer-title"><div><span className="eyebrow">EVIDENCE CONFLICTS</span><h2>Contradictions</h2></div><button aria-label="Close contradictions" onClick={() => setContradictionsOpen(false)}><X size={19} /></button></div>{spider.contradictions ? spider.contradictions.length ? spider.contradictions.map((claim) => <button key={claim.claim.id} className="conflict-item" onClick={() => { void spider.focusClaim(claim); setContradictionsOpen(false); setInspectorOpen(true); setFocusId(claim.claim.id) }}><span>{claim.status}</span><strong>{claim.claim.properties.claim_text}</strong><small>{claim.supporting_chunks.length} supporting · {claim.contradicting_chunks.length} contradicting passages</small></button>) : <p className="empty-small">No contested or contradicted claims in this snapshot.</p> : <p className="empty-small">Loading contradictions…</p>}</aside></div>}
    {loaderOpen && <GraphLoader key={`${spider.ids.researchId}:${spider.ids.snapshotId}`} initialResearchId={spider.ids.researchId ?? ''} initialSnapshotId={spider.ids.snapshotId ?? ''} busy={spider.phase === 'loading' || spider.phase === 'building'} error={spider.phase === 'error' ? spider.error : null} onLoad={(researchId, snapshotId) => void spider.load(researchId, snapshotId)} onBuild={(researchId, evidenceId) => void spider.build(researchId, evidenceId)} onClose={loaded ? () => setLoaderOpen(false) : developer ? onHome : undefined} />}
  </div>
}
