import { useState } from 'react'
import { ArrowRight, Database, Fingerprint, LoaderCircle, Network } from 'lucide-react'

interface Props {
  initialResearchId?: string; initialSnapshotId?: string
  busy: boolean; error: string | null
  onLoad: (researchId: string, snapshotId: string) => void
  onBuild: (researchId: string, evidenceId: string) => void
  onClose?: () => void
}
export function GraphLoader({ initialResearchId = '', initialSnapshotId = '', busy, error, onLoad, onBuild, onClose }: Props) {
  const [mode, setMode] = useState<'load' | 'build'>('load')
  const [researchId, setResearchId] = useState(initialResearchId)
  const [snapshotId, setSnapshotId] = useState(initialSnapshotId)
  const [evidenceId, setEvidenceId] = useState('')
  return <div className="loader-backdrop"><section className="graph-loader" aria-label="Open research graph">
    {onClose && <button className="loader-close" aria-label="Close graph loader" onClick={onClose}>×</button>}
    <div className="loader-orbit"><Network size={34} /></div>
    <span className="eyebrow">SPIDERMIND / PHASE 8A</span>
    <h1>Enter the research web.</h1>
    <p>Load a validated graph snapshot, or build one from a completed evidence job. SpiderMind uses your real Phase 7 records.</p>
    <div className="loader-tabs" role="tablist"><button role="tab" aria-selected={mode === 'load'} className={mode === 'load' ? 'active' : ''} onClick={() => setMode('load')}><Fingerprint size={16} /> Load snapshot</button><button role="tab" aria-selected={mode === 'build'} className={mode === 'build' ? 'active' : ''} onClick={() => setMode('build')}><Database size={16} /> Build graph</button></div>
    <form onSubmit={(event) => { event.preventDefault(); if (mode === 'load') onLoad(researchId.trim(), snapshotId.trim()); else onBuild(researchId.trim(), evidenceId.trim()) }}>
      <label>Research Job ID<input required aria-label="Research Job ID" placeholder="UUID of your research job" value={researchId} onChange={(event) => setResearchId(event.target.value)} /></label>
      {mode === 'load' ? <label>Graph Snapshot ID<input required aria-label="Graph Snapshot ID" placeholder="UUID of a Phase 7 snapshot" value={snapshotId} onChange={(event) => setSnapshotId(event.target.value)} /></label> : <label>Evidence Job ID<input required aria-label="Evidence Job ID" placeholder="UUID of a finished Phase 6 job" value={evidenceId} onChange={(event) => setEvidenceId(event.target.value)} /></label>}
      {error && <div className="form-error" role="alert">{error}</div>}
      <button className="primary-button" disabled={busy} type="submit">{busy ? <><LoaderCircle className="spin" size={17} /> {mode === 'load' ? 'Loading graph…' : 'Building graph…'}</> : <>{mode === 'load' ? 'Open graph' : 'Build and open'} <ArrowRight size={17} /></>}</button>
    </form>
    <small>Graph data stays in SpiderMind. This interface does not generate claims or answers.</small>
  </section></div>
}
