import { useCallback, useEffect, useRef, useState } from 'react'
import { researchApi } from './researchApi'
import { advanceRun, progressPath } from './researchRunMachine'
import { clearRun, restoreRun, saveRun } from './researchRunStorage'
import type { SpiderMindRun } from './researchRunTypes'
import { schedulePoll } from './polling'

export function useResearchRun(onNavigate: (path: string) => void) {
  const [run, setRun] = useState<SpiderMindRun | null>(restoreRun)
  const current = useRef(run)
  const busy = useRef(false)
  const generation = useRef(0)
  const pollErrors = useRef(0)
  const resultLoading = useRef(false)
  const resultHydrationAttempted = useRef(false)
  const [active, setActive] = useState(false)
  const commit = useCallback((value: SpiderMindRun) => {
    current.current = value; saveRun(value); setRun(value)
    if (value.researchJobId && window.location.pathname === '/') onNavigate(progressPath(value.researchJobId))
  }, [onNavigate])

  const start = useCallback((question: string) => {
    if (busy.current || (current.current && !['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED'].includes(current.current.stage))) return false
    const now = new Date().toISOString()
    generation.current += 1; pollErrors.current = 0; resultHydrationAttempted.current = false
    commit({ question: question.trim(), stage: 'CREATING_RESEARCH', startedAt: now, updatedAt: now })
    setActive(true)
    return true
  }, [commit])

  const retry = useCallback(() => {
    const value = current.current
    if (!value || value.stage === 'CREATING_RESEARCH' && !value.researchJobId) return
    if (value.resultDetailsError) {
      resultHydrationAttempted.current = false
      commit({ ...value, resultDetailsError: false })
      return
    }
    if (value.stage === 'PARTIAL' && value.graphSnapshotId && !value.graphValid) {
      commit({ ...value, stage: 'BUILDING_GRAPH', graphSnapshotId: undefined, error: undefined, retryStage: undefined })
      setActive(true)
      return
    }
    commit({ ...value, stage: value.retryStage ?? value.stage, error: undefined, retryStage: undefined })
    setActive(true)
  }, [commit])

  useEffect(() => {
    if (!active || !run || ['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED'].includes(run.stage)) return
    let cancelled = false
    const cancelTimer = schedulePoll(async () => {
      if (busy.current || cancelled) return
      busy.current = true
      const thisGeneration = generation.current
      try {
        const before = current.current
        if (!before) return
        const after = await advanceRun(before, (next) => { if (thisGeneration === generation.current) commit(next) })
        pollErrors.current = 0
        if (thisGeneration !== generation.current) return
        if (after === before) setRun({ ...after })
      } catch (cause) {
        if (thisGeneration !== generation.current) return
        const value = current.current
        if (value) {
          const isStatusRead = value.stage === 'RESEARCHING' || value.stage === 'INDEXING' && Boolean(value.indexJobId) || value.stage === 'RUNNING_AGENT' && Boolean(value.agentRunId) || value.stage === 'VERIFYING_EVIDENCE' && Boolean(value.evidenceJobId) || value.stage === 'BUILDING_GRAPH' && Boolean(value.graphSnapshotId)
          if (isStatusRead && pollErrors.current < 3) {
            pollErrors.current += 1
            commit({ ...value, warning: 'Connection interrupted. Retrying the current status check.' })
          } else commit({ ...value, stage: 'FAILED', retryStage: value.stage, error: cause instanceof Error ? cause.message : 'The current stage failed.' })
        }
      } finally { busy.current = false }
    }, { immediate: run.stage === 'CREATING_RESEARCH' || !run.researchJobId, failures: pollErrors.current })
    return () => { cancelled = true; cancelTimer() }
  }, [active, run, commit])

  const resume = useCallback(() => {
    const saved = restoreRun()
    if (saved) { current.current = saved; setRun(saved); setActive(true) }
  }, [])
  useEffect(() => {
    if (!run?.researchJobId || !['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED'].includes(run.stage)) return
    if (resultHydrationAttempted.current || resultLoading.current) return
    const missingSources = !run.sources
    const missingClaims = Boolean(run.evidenceJobId && !run.claims)
    const missingSummary = Boolean(run.graphValid && run.graphSnapshotId && !run.summary)
    if (!missingSources && !missingClaims && !missingSummary) return
    resultLoading.current = true
    resultHydrationAttempted.current = true
    const thisGeneration = generation.current
    void Promise.allSettled([
      missingSources ? researchApi.sources(run.researchJobId) : Promise.resolve(null),
      missingClaims ? researchApi.claims(run.researchJobId, run.evidenceJobId!) : Promise.resolve(null),
      missingSummary ? researchApi.graphSummary(run.researchJobId, run.graphSnapshotId!) : Promise.resolve(null),
    ]).then(([sources, claims, summary]) => {
      if (thisGeneration !== generation.current || !current.current) return
      const value = current.current
      const failed = sources.status === 'rejected' || claims.status === 'rejected' || summary.status === 'rejected'
      commit({ ...value,
        sources: sources.status === 'fulfilled' && sources.value ? sources.value.sources : value.sources,
        claims: claims.status === 'fulfilled' && claims.value ? claims.value.claims : value.claims,
        summary: summary.status === 'fulfilled' && summary.value ? summary.value : value.summary,
        resultDetailsError: failed,
      })
    }).finally(() => { resultLoading.current = false })
  }, [run, commit])
  const stop = useCallback(async () => {
    const value = current.current
    if (!value?.researchJobId || !value.agentRunId || value.stage !== 'RUNNING_AGENT') return
    try {
      await researchApi.cancelAgent(value.researchJobId, value.agentRunId)
      commit({ ...value, warning: 'Stop requested. The agent will finish its current step.' })
    } catch (cause) {
      commit({ ...value, warning: cause instanceof Error ? cause.message : 'Could not request a stop. Research is still running.' })
    }
  }, [commit])
  const reset = useCallback(() => { generation.current += 1; resultHydrationAttempted.current = false; clearRun(); current.current = null; setRun(null); setActive(false); onNavigate('/') }, [onNavigate])
  return { run, start, retry, resume, stop, reset }
}
