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
  const [zoom, setZoom] = useState(0.76)
  const [labels, setLabels] = useState(true)
  const [web, setWeb] = useState(true)
  const lastFocus = useRef<string | null>(null)
  const visible = useMemo(() => filterGraph(props.graph, props.filters), [props.graph, props.filters])
  const visual = useMemo(() => toFlowGraph(visible, props.selectedId, props.pathNodeIds, props.pathEdgeIds, zoom < 0.57 || !labels), [visible, props.selectedId, props.pathNodeIds, props.pathEdgeIds, zoom, labels])
  useEffect(() => {
    if (!props.focusId || lastFocus.current === props.focusId) return
    const target = visual.nodes.find((node) => node.id === props.focusId)
    if (target) {
      lastFocus.current = props.focusId
      void flow.setCenter(target.position.x + 105, target.position.y + 45, { zoom: 0.95, duration: 450 })
    }
  }, [props.focusId, visual.nodes, flow])
  useEffect(() => { if (props.fitSignal) void flow.fitView({ padding: 0.22, duration: 420 }) }, [props.fitSignal, flow])
  const handleClick: NodeMouseHandler<UiNode> = useCallback((_event, node) => props.onSelect(node.data.dto), [props.onSelect])
  const handleDoubleClick: NodeMouseHandler<UiNode> = useCallback((_event, node) => props.onExpand(node.id), [props.onExpand])
  return <div className={`graph-workspace ${web ? 'web-visible' : ''}`}>
    <div className="graph-workspace-label"><span className="eyebrow">LIVE RESEARCH TOPOLOGY</span><strong>Spider graph</strong><span>{visible.nodes.length} nodes in view · {visible.edges.length} connections{props.graph.truncated ? ' · bounded view' : ''}</span></div>
    <ReactFlow
      nodes={visual.nodes} edges={visual.edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
      onNodeClick={handleClick} onNodeDoubleClick={handleDoubleClick} onPaneClick={props.onClear}
      onMoveEnd={(_event, viewport) => setZoom(viewport.zoom)}
      fitView fitViewOptions={{ padding: 0.22, maxZoom: 0.85 }} minZoom={0.18} maxZoom={1.65}
      colorMode="dark"
      nodesDraggable={false} nodesConnectable={false} elementsSelectable
      proOptions={{ hideAttribution: true }} zoomOnDoubleClick={false}
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
      onReset={() => void flow.fitView({ padding: 0.22, duration: 420 })}
      labels={labels} onToggleLabels={() => setLabels((value) => !value)}
      web={web} onToggleWeb={() => setWeb((value) => !value)}
    />
  </div>
}

export function GraphWorkspace(props: Props) {
  return <ReactFlowProvider><Canvas {...props} /></ReactFlowProvider>
}
