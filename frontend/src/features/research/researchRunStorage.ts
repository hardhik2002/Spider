import type { SpiderMindRun } from './researchRunTypes'

const key = 'spidermind.activeResearchRun'
export function saveRun(run: SpiderMindRun): void {
  const metadata = { ...run }
  delete metadata.summary
  delete metadata.claims
  delete metadata.sources
  localStorage.setItem(key, JSON.stringify(metadata))
}
export function clearRun(): void { localStorage.removeItem(key) }
export function restoreRun(): SpiderMindRun | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? 'null') as SpiderMindRun | null
    if (!value || typeof value.question !== 'string' || typeof value.stage !== 'string') return null
    if (value.stage === 'CREATING_RESEARCH' && !value.researchJobId) return { ...value, stage: 'FAILED', error: 'The browser reloaded while the research request was being created. Its outcome cannot be verified. Start a new question to avoid a duplicate job.' }
    return value
  } catch { return null }
}
