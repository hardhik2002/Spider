import { afterEach, expect, it, vi } from 'vitest'
import { graphApi } from '../api/graphApi'

const live = import.meta.env.VITE_LIVE_TEST === '1'
afterEach(() => vi.unstubAllGlobals())

it.skipIf(!live)('reads a copied real Phase 6/7 graph through the frontend API client', async () => {
  const researchId = import.meta.env.VITE_LIVE_RESEARCH_ID
  const snapshotId = import.meta.env.VITE_LIVE_SNAPSHOT_ID
  if (!researchId || !snapshotId) throw new Error('Set VITE_LIVE_RESEARCH_ID and VITE_LIVE_SNAPSHOT_ID')
  const nativeFetch = globalThis.fetch.bind(globalThis)
  vi.stubGlobal('fetch', (path: string, init: RequestInit) =>
    nativeFetch(new URL(path, 'http://127.0.0.1:8000'), init))
  const status = await graphApi.status(researchId, snapshotId)
  expect(status.status).toBe('COMPLETED')
  const consistency = await graphApi.consistency(researchId, snapshotId)
  expect(consistency.status).toBe('VALID')
  const summary = await graphApi.summary(researchId, snapshotId)
  expect(summary.claims_total).toBeGreaterThan(0)
  const initial = await graphApi.subgraph(researchId, snapshotId, `research:${researchId}`, 2, 100, 'outgoing')
  const claim = initial.nodes.find((node) => node.type === 'CLAIM')
  expect(claim).toBeDefined()
  if (!claim || claim.type !== 'CLAIM') return
  const view = await graphApi.claim(researchId, snapshotId, claim.properties.claim_id)
  expect(view.claim.id).toBe(claim.id)
  expect(view.citations.length).toBeGreaterThan(0)
  const citation = view.citations[0]
  const detail = await graphApi.node(researchId, snapshotId, citation.id)
  expect(detail.node.type).toBe('CITATION')
  if (detail.node.type === 'CITATION') expect(detail.node.properties.exact_text).toBe(citation.properties.exact_text)
  const expanded = await graphApi.subgraph(researchId, snapshotId, claim.id, 2, 100, 'both')
  expect(expanded.nodes.length).toBeGreaterThan(initial.nodes.length - 1)
  const paths = await graphApi.path(researchId, snapshotId, `research:${researchId}`, claim.id)
  expect(paths.count).toBeGreaterThan(0)
  const contradictions = await graphApi.contradictions(researchId, snapshotId)
  expect(Array.isArray(contradictions.claims)).toBe(true)
})
