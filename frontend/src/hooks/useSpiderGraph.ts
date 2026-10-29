import { useCallback, useMemo, useRef, useState } from 'react'
import { graphApi } from '../api/graphApi'
import type {
  ClaimViewDto, ConsistencyDto, GraphNodeDto, GraphStatusDto, GraphSummaryDto,
  NodeDetailDto, PathDto, SubgraphDto,
} from '../types/graph'
import { mergeSubgraphs } from '../features/graph/graphAdapter'

type LoadState = 'idle' | 'loading' | 'building' | 'ready' | 'error'
const emptyGraph: SubgraphDto = { nodes: [], edges: [], truncated: false }

function initialIds(): { researchId: string; snapshotId: string } {
  const match = window.location.pathname.match(/^\/research\/([^/]+)\/graph\/([^/]+)/)
  if (match) return { researchId: decodeURIComponent(match[1]), snapshotId: decodeURIComponent(match[2]) }
  try { return JSON.parse(localStorage.getItem('spidermind:last-graph') ?? '{}') as { researchId: string; snapshotId: string } }
  catch { return { researchId: '', snapshotId: '' } }
}

export function useSpiderGraph() {
  const [ids, setIds] = useState(initialIds)
  const [phase, setPhase] = useState<LoadState>('idle')
  const [error, setError] = useState<string | null>(null)
  const [graph, setGraph] = useState<SubgraphDto>(emptyGraph)
  const [status, setStatus] = useState<GraphStatusDto | null>(null)
  const [summary, setSummary] = useState<GraphSummaryDto | null>(null)
  const [consistency, setConsistency] = useState<ConsistencyDto | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<NodeDetailDto | null>(null)
  const [claimView, setClaimView] = useState<ClaimViewDto | null>(null)
  const [contradictions, setContradictions] = useState<ClaimViewDto[] | null>(null)
  const [path, setPath] = useState<PathDto | null>(null)
  const [expandingId, setExpandingId] = useState<string | null>(null)
  const selectionRequest = useRef(0)
  const loadRequest = useRef(0)

  const selectedNode = useMemo(
    () => graph.nodes.find((node) => node.id === selectedId) ?? detail?.node ?? null,
    [graph.nodes, selectedId, detail],
  )

  const load = useCallback(async (researchId: string, snapshotId: string) => {
    const request = ++loadRequest.current
    setPhase('loading'); setError(null); setGraph(emptyGraph); setSelectedId(null)
    setStatus(null); setSummary(null); setConsistency(null); setContradictions(null)
    setClaimView(null); setDetail(null); setPath(null)
    try {
      let current = await graphApi.status(researchId, snapshotId)
      setStatus(current)
      for (let attempt = 0; attempt < 60 && (current.status === 'PENDING' || current.status === 'RUNNING'); attempt++) {
        if (request !== loadRequest.current) return
        setPhase('building')
        await new Promise((resolve) => setTimeout(resolve, 1500))
        current = await graphApi.status(researchId, snapshotId)
        setStatus(current)
      }
      if (request !== loadRequest.current) return
      if (current.status !== 'COMPLETED') throw new Error(`Graph build is ${current.status.toLowerCase()}. Open status or retry the build.`)
      const [nextSummary, nextConsistency, first] = await Promise.all([
        graphApi.summary(researchId, snapshotId),
        graphApi.consistency(researchId, snapshotId),
        graphApi.subgraph(researchId, snapshotId, `research:${researchId}`, 2, 100, 'outgoing'),
      ])
      setSummary(nextSummary); setConsistency(nextConsistency); setGraph(first)
      setIds({ researchId, snapshotId }); setPhase('ready')
      localStorage.setItem('spidermind:last-graph', JSON.stringify({ researchId, snapshotId }))
      window.history.replaceState(null, '', `/research/${encodeURIComponent(researchId)}/graph/${encodeURIComponent(snapshotId)}`)
    } catch (cause) {
      if (request !== loadRequest.current) return
      setError(cause instanceof Error ? cause.message : 'Could not load graph')
      setPhase('error')
    }
  }, [])

  const build = useCallback(async (researchId: string, evidenceId: string) => {
    setPhase('building'); setError(null)
    try {
      const result = await graphApi.build(researchId, evidenceId)
      await load(researchId, result.graph_snapshot_id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not start graph build')
      setPhase('error')
    }
  }, [load])

  const expand = useCallback(async (nodeId: string) => {
    if (!ids.researchId || !ids.snapshotId) return
    setExpandingId(nodeId); setError(null)
    try {
      const more = await graphApi.subgraph(ids.researchId, ids.snapshotId, nodeId, 2, 100, 'both')
      setGraph((current) => mergeSubgraphs(current, more))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not expand node')
    } finally { setExpandingId(null) }
  }, [ids])

  const select = useCallback(async (node: GraphNodeDto) => {
    if (!ids.researchId || !ids.snapshotId) return
    const request = ++selectionRequest.current
    setSelectedId(node.id); setDetail(null); setClaimView(null); setError(null)
    try {
      const [nextDetail, nextClaim] = await Promise.all([
        graphApi.node(ids.researchId, ids.snapshotId, node.id),
        node.type === 'CLAIM' ? graphApi.claim(ids.researchId, ids.snapshotId, node.properties.claim_id) : Promise.resolve(null),
      ])
      if (request !== selectionRequest.current) return
      setDetail(nextDetail); setClaimView(nextClaim)
    } catch (cause) {
      if (request !== selectionRequest.current) return
      setError(cause instanceof Error ? cause.message : 'Could not load node detail')
    }
  }, [ids])

  const viewFullPassage = useCallback(async (nodeId: string) => {
    if (!ids.researchId || !ids.snapshotId) return
    try { setDetail(await graphApi.node(ids.researchId, ids.snapshotId, nodeId, true)) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not load passage') }
  }, [ids])

  const focusPath = useCallback(async (from: string, to: string) => {
    if (!ids.researchId || !ids.snapshotId) return
    setError(null)
    try {
      const response = await graphApi.path(ids.researchId, ids.snapshotId, from, to)
      const first = response.paths[0] ?? null
      setPath(first)
      if (first) setGraph((current) => mergeSubgraphs(current, { ...first, truncated: false }))
      else setError('No directed path exists between those nodes.')
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not find path') }
  }, [ids])

  const loadContradictions = useCallback(async () => {
    if (!ids.researchId || !ids.snapshotId) return
    try { setContradictions((await graphApi.contradictions(ids.researchId, ids.snapshotId)).claims) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not load contradictions') }
  }, [ids])

  const focusClaim = useCallback(async (claim: ClaimViewDto) => {
    setGraph((current) => mergeSubgraphs(current, { nodes: claim.nodes, edges: claim.edges, truncated: false }))
    await select(claim.claim)
    await focusPath(`research:${ids.researchId}`, claim.claim.id)
  }, [focusPath, ids.researchId, select])

  return {
    ids, phase, error, graph, status, summary, consistency, selectedId, selectedNode,
    detail, claimView, contradictions, path, expandingId,
    load, build, expand, select, viewFullPassage, focusPath, loadContradictions, focusClaim,
    clearSelection: () => { selectionRequest.current += 1; setSelectedId(null); setDetail(null); setClaimView(null) },
    clearPath: () => setPath(null),
    clearError: () => setError(null),
    refreshConsistency: async () => {
      if (!ids.researchId || !ids.snapshotId) return
      try { setConsistency(await graphApi.consistency(ids.researchId, ids.snapshotId)); setError(null) }
      catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not check graph consistency') }
    },
  }
}
