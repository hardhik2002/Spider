import { useState } from 'react'
import { ArrowRight, LoaderCircle, Network, Search, Sparkles } from 'lucide-react'
import { spiderAssetUrl } from '../graph/SpiderHub'
import type { SpiderMindRun } from '../../features/research/researchRunTypes'

const examples = ['Compare GraphRAG and traditional RAG architectures', 'How does agentic RAG improve research?', 'What is hybrid retrieval in RAG?']
export function ResearchHome({ run, onStart, onResume, onDeveloper }: { run: SpiderMindRun | null; onStart: (question: string) => boolean; onResume: () => void; onDeveloper: () => void }) {
  const [question, setQuestion] = useState('')
  const [error, setError] = useState('')
  const submitting = run?.stage === 'CREATING_RESEARCH'
  const submit = () => {
    const value = question.trim()
    if (value.length < 8 || value.length > 2000 || value.split(/\s+/).length < 2) { setError('Enter a research question with at least two words (8–2000 characters).'); return }
    if (submitting) return
    setError('')
    if (!onStart(value)) setError('A research run is already active. Continue it below.')
  }
  return <div className="research-page"><header className="research-header"><a className="research-brand" href="/" aria-label="SpiderMind home"><span>{spiderAssetUrl && <img src={spiderAssetUrl} alt="" />}</span><strong>Spider<span>Mind</span></strong></a><button className="research-header-link" onClick={onDeveloper}>Developer Tools</button></header>
    <main className="research-home"><div className="home-ambient" aria-hidden="true">{spiderAssetUrl && <img src={spiderAssetUrl} alt="" />}</div><div className="home-content"><div className="home-kicker"><Network size={15} /> TRACE EVERY CLAIM TO ITS SOURCE</div><h1>Research the web.<br /><em>Trace the evidence.</em></h1><p className="home-lead">Ask a question. SpiderMind searches public sources, checks the evidence, and builds a graph you can explore.</p>
      <section className="question-card" aria-label="Start research"><label htmlFor="research-question"><Search size={18} /> YOUR RESEARCH QUESTION</label><textarea id="research-question" autoFocus placeholder="Ask a research question..." value={question} onChange={(event) => { setQuestion(event.target.value); setError('') }} onKeyDown={(event) => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); submit() } }} maxLength={2000} rows={4} /><div className="question-actions"><span>Ctrl / ⌘ + Enter to start</span><button className="primary-button" onClick={submit} disabled={submitting}>{submitting ? <><LoaderCircle className="spin" size={16} /> Starting research…</> : <>Research <ArrowRight size={17} /></>}</button></div>{error && <p className="form-error" role="alert">{error}</p>}{run?.stage === 'FAILED' && !run.researchJobId && run.error && <p className="form-error" role="alert">{run.error}</p>}</section>
      <div className="examples"><span><Sparkles size={14} /> TRY A QUESTION</span><div>{examples.map((item) => <button key={item} onClick={() => setQuestion(item)}>{item} <ArrowRight size={13} /></button>)}</div></div>
      {run?.researchJobId && <button className="resume-card" onClick={onResume}><span>CONTINUE RESEARCH</span><strong>{run.question}</strong><small>{run.stage.replaceAll('_', ' ').toLowerCase()} <ArrowRight size={14} /></small></button>}
    </div></main><footer className="research-footer">SPIDERMIND — MAP KNOWLEDGE. FIND TRUTH.</footer></div>
}
