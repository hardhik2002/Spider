import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background, BackgroundVariant, ReactFlow, ReactFlowProvider,
  useReactFlow, type NodeMouseHandler,
} from '@xyflow/react'
import type { GraphNodeDto, SubgraphDto } from '../../types/graph'
import { filterGraph, toFlowGraph, type GraphFilters, type UiNode } from '../../features/graph/graphAdapter'
import { GraphNodeCard } from './GraphNodeCard'
import { SemanticEdge } from './SemanticEdge'
import { SpiderHub } from './SpiderHub'
import { GraphToolbar } from './GraphToolbar'
import { GraphLegend } from './GraphLegend'

const nodeTypes = { semantic: GraphNodeCard }
const edgeTypes = { semantic: SemanticEdge }

interface Props {
  graph: SubgraphDto; filters: GraphFilters; selectedId: string | null
  pathNodeIds: ReadonlySet<string>; pathEdgeIds: ReadonlySet<string>
  focusId: string | null; fitSignal: number; onSelect: (node: GraphNodeDto) => void; onExpand: (id: string) => void
  onClear: () => void; expandingId: string | null
}

function Canvas(props: Props) {
  const flow = useReactFlow<UiNode>()
  const workspaceRef = useRef<HTMLDivElement>(null)
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 })
  const [zoom, setZoom] = useState(0.76)
  const [labels, setLabels] = useState(true)
  const [web, setWeb] = useState(true)
  const lastFocus = useRef<string | null>(null)
  const initialized = useRef(false)
  const layoutZoom = dimensions.width >= 1000 ? 0.9 :
    dimensions.width >= 780 ? 0.82 : dimensions.width >= 710 ? 0.75 : 0.68
  const visible = useMemo(() => filterGraph(props.graph, props.filters), [props.graph, props.filters])
  const visual = useMemo(() => toFlowGraph(
    visible, props.selectedId, props.pathNodeIds, props.pathEdgeIds, zoom < 0.57 || !labels,
    dimensions.width && dimensions.height ? { ...dimensions, zoom: layoutZoom } : undefined,
  ), [visible, props.selectedId, props.pathNodeIds, props.pathEdgeIds, zoom, labels, dimensions, layoutZoom])
  useEffect(() => {
    const element = workspaceRef.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setDimensions((current) => current.width === width && current.height === height ? current : { width, height })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    if (!props.graph.nodes.length) { initialized.current = false; return }
    if (!dimensions.width || !dimensions.height || initialized.current) return
    initialized.current = true
    void flow.setCenter(0, 0, { zoom: layoutZoom, duration: 0 })
  }, [props.graph.nodes.length, dimensions, layoutZoom, flow])
  useEffect(() => {
    if (!props.focusId) { lastFocus.current = null; return }
    if (lastFocus.current === props.focusId) return
    const target = visual.nodes.find((node) => node.id === props.focusId)
    if (target && dimensions.width && dimensions.height) {
      lastFocus.current = props.focusId
      const targetZoom = dimensions.width < 700 ? 0.68 : 0.82
      const targetX = target.data.dto.type === 'RESEARCH' ? dimensions.width / 2 :
        target.position.x < 0 ? Math.max(110, dimensions.width * 0.15) : Math.min(dimensions.width - 110, dimensions.width * 0.85)
      const targetY = target.data.dto.type === 'RESEARCH' ? Math.max(95, dimensions.height * 0.14) : dimensions.height * 0.4
      void flow.setViewport({
        x: targetX - (target.position.x + 112) * targetZoom,
        y: targetY - (target.position.y + 45) * targetZoom,
        zoom: targetZoom,
      }, { duration: 450 })
    }
  }, [props.focusId, visual.nodes, flow, dimensions])
  useEffect(() => { if (props.fitSignal) void flow.fitView({ padding: 0.22, duration: 420 }) }, [props.fitSignal, flow])
  const handleClick: NodeMouseHandler<UiNode> = useCallback((_event, node) => props.onSelect(node.data.dto), [props.onSelect])
  const handleDoubleClick: NodeMouseHandler<UiNode> = useCallback((_event, node) => props.onExpand(node.id), [props.onExpand])
  return <div ref={workspaceRef} className={`graph-workspace ${web ? 'web-visible' : ''}`}>
    <div className="graph-workspace-label"><span className="eyebrow">LIVE RESEARCH TOPOLOGY</span><strong>Spider graph</strong><span>{visible.nodes.length} nodes in view · {visible.edges.length} connections{props.graph.truncated ? ' · bounded view' : ''}</span></div>
    <ReactFlow
      nodes={visual.nodes} edges={visual.edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
      onNodeClick={handleClick} onNodeDoubleClick={handleDoubleClick} onPaneClick={props.onClear}
      onMoveEnd={(_event, viewport) => setZoom(viewport.zoom)}
      minZoom={0.18} maxZoom={1.65}
      colorMode="dark"
      nodesDraggable={false} nodesConnectable={false} elementsSelectable
      zoomOnDoubleClick={false}
      aria-label="Interactive research graph"
    >
      <Background variant={BackgroundVariant.Dots} color="rgba(255,255,255,.08)" gap={28} size={1} />
    </ReactFlow>
    <SpiderHub />
    {props.expandingId && <div className="graph-inline-loading" role="status">Expanding neighborhood…</div>}
    <GraphLegend />
    <GraphToolbar
      zoom={zoom} onZoomIn={() => void flow.zoomIn({ duration: 240 })}
      onZoomOut={() => void flow.zoomOut({ duration: 240 })}
      onFit={() => void flow.fitView({ padding: 0.22, duration: 420 })}
      onReset={() => void flow.setCenter(0, 0, { zoom: layoutZoom, duration: 420 })}
      labels={labels} onToggleLabels={() => setLabels((value) => !value)}
      web={web} onToggleWeb={() => setWeb((value) => !value)}
    />
  </div>
}

export function GraphWorkspace(props: Props) {
  return <ReactFlowProvider><Canvas {...props} /></ReactFlowProvider>
}
