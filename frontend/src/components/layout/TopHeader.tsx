import { Activity, Crosshair, RefreshCw, Search, Waypoints } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { GraphNodeDto } from '../../types/graph'
import { nodeNames } from '../../features/graph/visuals'
import { spiderAssetUrl } from '../graph/SpiderHub'

interface Props {
  nodes: GraphNodeDto[]; onFocus: (node: GraphNodeDto) => void
  onRefresh: () => void; onFit: () => void; loaded: boolean; consistency: string | null
}

export function TopHeader({ nodes, onFocus, onRefresh, onFit, loaded, consistency }: Props) {
  const [query, setQuery] = useState('')
  const results = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (needle.length < 2) return []
    return nodes.filter((node) => {
      const text = [node.label, node.type, ...Object.values(node.properties).filter((value) => typeof value === 'string')].join(' ').toLowerCase()
      return text.includes(needle)
    }).slice(0, 8)
  }, [nodes, query])
  return <header className="top-header">
    <div className="brand" aria-label="SpiderMind">
      <span className="brand-mark">{spiderAssetUrl ? <img src={spiderAssetUrl} alt="" /> : <Waypoints size={22} strokeWidth={1.6} />}</span>
      <span className="brand-wordmark"><strong>Spider<span>Mind</span></strong><small>INTERACTIVE RESEARCH GRAPH</small></span>
    </div>
    <div className="header-search">
      <Search size={17} aria-hidden="true" />
      <input aria-label="Search loaded graph" placeholder="Search claims, sources, citations..." value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => {
        if (event.key === 'Enter' && results[0]) { onFocus(results[0]); setQuery('') }
        if (event.key === 'Escape') setQuery('')
      }} />
      <span className="search-shortcut">LOCAL GRAPH</span>
      {query.trim().length >= 2 && <div className="search-results" role="listbox" aria-label="Graph search results">
        {results.length ? results.map((node) => <button role="option" aria-selected="false" key={node.id} onClick={() => { onFocus(node); setQuery('') }}>
          <span>{nodeNames[node.type]}</span><strong>{node.label}</strong>
        </button>) : <p>No loaded nodes match “{query}”. Expand the graph to search more.</p>}
      </div>}
    </div>
    <div className="header-actions">
      <span className={`header-health ${consistency === 'INVALID' ? 'is-bad' : ''}`}><Activity size={15} />{loaded ? consistency ?? 'CHECKING' : 'NO GRAPH'}</span>
      <button aria-label="Refresh graph" title="Refresh graph" onClick={onRefresh} disabled={!loaded}><RefreshCw size={18} /></button>
      <button aria-label="Fit graph view" title="Fit view" onClick={onFit} disabled={!loaded}><Crosshair size={18} /></button>
    </div>
  </header>
}
