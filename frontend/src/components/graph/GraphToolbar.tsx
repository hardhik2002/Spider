import { Crosshair, Eye, EyeOff, Minus, Plus, RotateCcw, ScanSearch } from 'lucide-react'

interface Props {
  zoom: number; onZoomIn: () => void; onZoomOut: () => void; onFit: () => void
  onReset: () => void; labels: boolean; onToggleLabels: () => void
  web: boolean; onToggleWeb: () => void
}
export function GraphToolbar(props: Props) {
  return <div className="graph-toolbar" role="toolbar" aria-label="Graph controls">
    <button aria-label="Zoom out" title="Zoom out" onClick={props.onZoomOut}><Minus size={17} /></button>
    <span className="zoom-readout">{Math.round(props.zoom * 100)}%</span>
    <button aria-label="Zoom in" title="Zoom in" onClick={props.onZoomIn}><Plus size={17} /></button>
    <span className="toolbar-divider" />
    <button aria-label="Fit graph to view" title="Fit view" onClick={props.onFit}><ScanSearch size={17} /></button>
    <button aria-label="Reset graph layout" title="Reset layout" onClick={props.onReset}><RotateCcw size={17} /></button>
    <span className="toolbar-divider" />
    <button aria-label={props.labels ? 'Hide node labels' : 'Show node labels'} aria-pressed={props.labels} title="Toggle labels" onClick={props.onToggleLabels}>{props.labels ? <Eye size={17} /> : <EyeOff size={17} />}</button>
    <button aria-label={props.web ? 'Hide web background' : 'Show web background'} aria-pressed={props.web} title="Toggle web" onClick={props.onToggleWeb}><Crosshair size={17} /></button>
  </div>
}
