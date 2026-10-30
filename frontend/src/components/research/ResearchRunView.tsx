import { ArrowLeft, ArrowRight, AlertTriangle, CircleCheck, CircleDot, Circle, LoaderCircle, Network, Square } from 'lucide-react'
import { spiderAssetUrl } from '../graph/SpiderHub'
import { stageLabel } from '../../features/research/researchRunMachine'
import type { ResearchStage, SpiderMindRun } from '../../features/research/researchRunTypes'
import { safeExternalUrl } from '../../utils/safety'

const stages: { stage: ResearchStage; label: string }[] = [
  { stage: 'RESEARCHING', label: 'Search and read public sources' },
  { stage: 'INDEXING', label: 'Index evidence for retrieval' },
  { stage: 'RUNNING_AGENT', label: 'Check for missing information' },
  { stage: 'VERIFYING_EVIDENCE', label: 'Verify claims and citations' },
  { stage: 'BUILDING_GRAPH', label: 'Build the research graph' },
]
const order = stages.map((item) => item.stage)
const isTerminal = (stage: ResearchStage) => ['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED'].includes(stage)
const researchSteps = [
  { key: 'planning', label: 'Understanding the question' },
  { key: 'searching', label: 'Searching the web' },
  { key: 'scoring', label: 'Selecting public sources' },
  { key: 'crawling', label: 'Reading source pages' },
]

export function ResearchRunView({ run, onRetry, onStop, onReset, onExplore }: { run: SpiderMindRun | null; onRetry: () => void; onStop: () => void; onReset: () => void; onExplore: () => void }) {
  if (!run) return <div className="research-page"><main className="run-empty"><h1>Research run not found in this browser</h1><p>Open the research home to start a new question.</p><button className="primary-button" onClick={onReset}>Research home <ArrowRight size={16} /></button></main></div>
  const done = run.stage === 'COMPLETED' || run.stage === 'PARTIAL'
  const current = order.indexOf(run.stage)
  const completeThrough = isTerminal(run.stage) ? (run.graphSnapshotId ? 4 : run.evidenceJobId ? 3 : run.agentRunId ? 2 : run.indexJobId ? 1 : run.researchJobId ? 0 : -1) : current - 1
  const counts = [
    ['Search results', run.searchResults], ['Sources selected', run.sourcesSelected], ['Pages read', run.pagesCrawled],
    ['Documents indexed', run.documentsIndexed], ['Claims extracted', run.claimsExtracted], ['Citations validated', run.citationsValidated],
  ].filter((entry) => typeof entry[1] === 'number')
  return <div className="research-page"><header className="research-header"><button className="research-brand" onClick={onReset}><span>{spiderAssetUrl && <img src={spiderAssetUrl} alt="" />}</span><strong>Spider<span>Mind</span></strong></button><span className="research-header-status"><i /> {stageLabel(run.stage)}</span></header>
    <main className="run-main"><button className="back-link" onClick={onReset}><ArrowLeft size={15} /> New research</button><div className="run-heading"><div className="home-kicker"><Network size={15} /> RESEARCH WORKSPACE</div><h1>{done ? 'Research results' : stageLabel(run.stage)}</h1><p className="run-question">“{run.question}”</p></div>
    {run.warning && <div className="run-warning" role="status"><AlertTriangle size={17} />{run.warning}</div>}
    {run.error && <div className="run-error" role="alert"><AlertTriangle size={18} /><div><strong>{run.stage === 'FAILED' ? 'This stage needs attention' : 'Research stopped'}</strong><p>{run.error}</p></div></div>}
    {run.resultDetailsError && <div className="run-warning" role="alert"><AlertTriangle size={17} />Some recorded result details could not be loaded. Check the backend and retry.</div>}
    {done && run.summary && <section className="result-overview"><div><span className="eyebrow">GROUNDED OVERVIEW</span><h2>Evidence you can inspect</h2><p>These are recorded claim decisions and source counts from the verification pipeline. A synthesized answer is planned for a later release.</p></div><div className="result-stats">{[['Verified claims', run.summary.claims_corroborated + run.summary.claims_supported], ['Contested claims', run.summary.claims_contested], ['Citations', run.summary.citations_total], ['Documents', run.summary.documents_total], ['Source groups', run.summary.source_groups_total]].map(([label, value]) => <div key={label}><strong>{value}</strong><span>{label}</span></div>)}</div>{run.graphValid && <button className="primary-button explore-graph" onClick={onExplore}>Explore Spider Graph <ArrowRight size={17} /></button>}</section>}
    {done && run.claims && <section className="result-claims"><div className="section-title"><span className="eyebrow">CLAIM LEDGER</span><h2>Verified claims</h2></div>{run.claims.length ? run.claims.map((claim) => <article key={claim.id}><span className={`claim-status claim-${claim.status.toLowerCase()}`}>{claim.status.replaceAll('_', ' ')}</span><h3>{claim.claim}</h3><p>{claim.support_count} supporting passages · {claim.contradiction_count} contradicting passages · {claim.independent_support_groups} independent supporting groups</p></article>) : <p>No claims were recorded for this run.</p>}</section>}
    {run.sources && run.sources.length > 0 && <section className="result-claims source-list"><div className="section-title"><span className="eyebrow">PUBLIC SOURCES</span><h2>Sources read or attempted</h2></div>{run.sources.map((source) => <article key={source.url}><span className="claim-status">{source.crawl_status ?? 'NOT CRAWLED'}</span><h3>{source.title || source.url}</h3><p>{source.pages.length} pages recorded · {source.pages.filter((page) => page.status === 'COMPLETED').length} completed</p>{safeExternalUrl(source.url) && <a href={safeExternalUrl(source.url)!} target="_blank" rel="noopener noreferrer">Open public source <ArrowRight size={13} /></a>}</article>)}</section>}
    <div className="run-grid"><section className="run-panel"><div className="section-title"><span className="eyebrow">PIPELINE</span><h2>Research progress</h2></div><ol className="stage-list">{stages.map((item, index) => { const finished = index <= completeThrough && (done || current > index); const partial = finished && run.partialStages?.includes(item.stage); const active = !isTerminal(run.stage) && current === index; return <li key={item.stage} className={partial ? 'partial' : finished ? 'finished' : active ? 'active' : ''}><div className="stage-line">{partial ? <AlertTriangle size={19} /> : finished ? <CircleCheck size={19} /> : active ? <CircleDot size={19} /> : <Circle size={19} />}<span>{item.label}{partial ? ' · partial' : ''}</span>{active && <LoaderCircle className="spin" size={16} />}</div>{item.stage === 'RESEARCHING' && run.stage === 'RESEARCHING' && <ol className="research-substeps">{researchSteps.map((step, stepIndex) => { const currentStep = researchSteps.findIndex((entry) => entry.key === run.researchStep); return <li key={step.key} className={stepIndex < currentStep ? 'finished' : stepIndex === currentStep ? 'active' : ''}>{stepIndex < currentStep ? <CircleCheck size={14} /> : stepIndex === currentStep ? <CircleDot size={14} /> : <Circle size={14} />}{step.label}</li> })}</ol>}</li> })}</ol>{run.agentStopReason && <p className="stage-detail">Agent stop reason: {run.agentStopReason.replaceAll('_', ' ')}</p>}{run.evidenceStep && run.stage === 'VERIFYING_EVIDENCE' && <p className="stage-detail">Verification step: {run.evidenceStep.replaceAll('_', ' ').toLowerCase()}</p>}</section>
      <section className="run-panel"><div className="section-title"><span className="eyebrow">RECORDED COUNTS</span><h2>What SpiderMind found</h2></div><div className="run-counts">{counts.length ? counts.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>) : <p>Counts will appear as the backend reports them.</p>}</div></section></div>
    <div className="run-actions">{!isTerminal(run.stage) && run.stage === 'RUNNING_AGENT' && <button className="secondary-button" onClick={onStop}><Square size={14} /> Stop research</button>}{run.resultDetailsError && <button className="secondary-button" onClick={onRetry}>Retry result details</button>}{run.stage === 'FAILED' && run.retryStage && <button className="primary-button" onClick={onRetry}>Retry current stage <ArrowRight size={16} /></button>}{run.stage === 'PARTIAL' && run.graphSnapshotId && !run.graphValid && <button className="secondary-button" onClick={onRetry}>Retry graph build</button>}</div>
    </main><footer className="research-footer">SPIDERMIND — MAP KNOWLEDGE. FIND TRUTH.</footer></div>
}
