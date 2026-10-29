import { edgeVisuals } from '../../features/graph/visuals'

export function GraphLegend() {
  return <div className="graph-legend" aria-label="Graph legend">
    <span><i className="legend-dot selected" /> Selected</span>
    <span><i className="legend-line" style={{ background: edgeVisuals.SUPPORTED_BY.color }} /> Support</span>
    <span><i className="legend-line" style={{ background: edgeVisuals.CONTRADICTED_BY.color }} /> Contradiction</span>
    <span><i className="legend-line" style={{ background: edgeVisuals.PARTIALLY_SUPPORTED_BY.color }} /> Partial</span>
  </div>
}
