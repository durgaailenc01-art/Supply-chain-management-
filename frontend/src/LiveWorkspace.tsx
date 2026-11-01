import { useState, type FormEvent } from 'react'
import { api, type LiveResult, type Warehouse } from './api'

const fmt = (n: number | null | undefined, digits = 0) => n == null ? '—' : new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(n)

function localDateTimeInput(value: Date): string {
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 19)
}

export default function LiveWorkspace({ warehouses }: { warehouses: Warehouse[] }) {
  const [open, setOpen] = useState(false)
  const [warehouseName, setWarehouseName] = useState('')
  const [snapshotLocal, setSnapshotLocal] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<LiveResult | null>(null)
  const [selectedSku, setSelectedSku] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    if (!warehouseName.trim() || !snapshotLocal || files.length === 0) {
      setError('Enter the warehouse, snapshot time, and select its data files.')
      return
    }
    setRunning(true)
    try {
      const analysis = await api.optimizeUpload(files, warehouseName.trim(), new Date(snapshotLocal).toISOString())
      setResult(analysis)
      setSelectedSku(null)
      setOpen(false)
      document.getElementById('live')?.scrollIntoView({ behavior: 'smooth' })
    } catch (issue) {
      const message = (issue as Error).message
      const snapshots = [...message.matchAll(/row snapshot_at=(\S+) differs from entered snapshot_at=/g)].map(match => match[1])
      const detectedSnapshots = [...new Set(snapshots)]
      if (detectedSnapshots.length === 1) {
        const detected = new Date(detectedSnapshots[0])
        if (!Number.isNaN(detected.getTime())) {
          const previousInput = snapshotLocal
          const suggestedInput = localDateTimeInput(detected)
          setSnapshotLocal(suggestedInput)
          setError(`The inventory and assignment rows use ${detected.toLocaleString()} in your time zone, while the entered time was ${new Date(previousInput).toLocaleString()}. I set the field to the timestamp found in the CSVs. Review it and select “Process dataset” again.`)
        } else {
          setError(message)
        }
      } else {
        setError(message)
      }
    } finally {
      setRunning(false)
    }
  }

  const selected = result?.recommendations.find(row => row.sku_id === selectedSku)
  const change = result?.metrics.optimization_score
  const modeledMetersSaved = result && change != null ? result.metrics.baseline_pick_distance_m * change / 100 : null
  return <section className="live-workspace" id="live">
    <div className="workflow-steps"><span className={open && !running ? 'active' : ''}>1 · Upload data</span><span className={running ? 'active' : ''}>2 · Process dataset</span><span className={!open && result ? 'active' : ''}>3 · Review results</span></div>
    <div className="live-header">
      <div><span className="eyebrow">YOUR WAREHOUSE DATA</span><h2>Optimize an uploaded snapshot</h2><p>Upload warehouse files, wait for the checks and route calculations, then review each proposed move. Stock is never moved automatically.</p></div>
      <button className="button primary" onClick={() => { setError(''); setOpen(true) }}>{result ? 'Upload another dataset' : 'Upload warehouse data'}</button>
    </div>
    {!open && (!result ? <div className="live-empty"><strong>No uploaded analysis yet</strong><span>Start with warehouse, zone, bin, SKU, package, bin inventory, assignment, aisle layout and demand files. CSV and XLSX/XLSM are accepted. A canonical manifest is recommended.</span></div> : <>
      <div className="live-meta"><strong>{result.warehouse_name}</strong><span>Snapshot {new Date(result.snapshot_at).toLocaleString()}</span><span>{result.demand_source}</span><span>{result.statistics.inventory_rows} stock rows · {result.statistics.candidate_locations} candidate bins</span></div>
      {result.validation.warnings.filter(warning => ['post_snapshot_demand', 'future_snapshot_preview'].includes(warning.code)).map((warning, index) => <div className="live-data-warning" role="status" key={`${warning.code}-${index}`}>{warning.detail}</div>)}
      {result.validation.warnings.some(warning => warning.code === 'unsafe_pack_capacity') && <details className="live-data-warning"><summary>{result.validation.warnings.filter(warning => warning.code === 'unsafe_pack_capacity').length} unsafe capacity certificate(s) excluded · review before operational use</summary><ul>{result.validation.warnings.filter(warning => warning.code === 'unsafe_pack_capacity').map((warning, index) => <li key={index}>{warning.detail}</li>)}</ul></details>}
      <div className="live-kpis">
        <div><span>Estimated pick travel saved</span><strong className={modeledMetersSaved != null && modeledMetersSaved < 0 ? 'caution' : 'good'}>{modeledMetersSaved == null ? '—' : `${fmt(modeledMetersSaved, 0)} m`}</strong><small>Modeled against the current layout; unassigned picks receive no savings credit</small></div>
        <div><span>Assigned SKUs</span><strong>{fmt(result.metrics.assigned_skus)}</strong><small>{fmt(result.metrics.unassigned_skus)} unassigned · {fmt(result.metrics.unserved_pick_count)} picks affected</small></div>
        <div><span>Proposed utilization</span><strong>{result.metrics.proposed_utilization_pct == null ? '—' : `${fmt(result.metrics.proposed_utilization_pct, 1)}%`}</strong><small>{fmt(result.metrics.baseline_utilization_pct, 1)}% current · candidate bins only</small></div>
        <div><span>Recommendation accuracy</span><strong>Not measured</strong><small>No held-out, measured warehouse outcomes were provided</small></div>
      </div>
      <div className="live-insights"><div><strong>Modeled picking distance</strong><p>{fmt(result.metrics.comparable_baseline_m, 1)} m current → {fmt(result.metrics.comparable_proposed_m, 1)} m proposed on comparable SKUs.</p></div><div><strong>Why some candidate bins were rejected</strong><p>{Object.keys(result.metrics.constraint_violations).length ? Object.entries(result.metrics.constraint_violations).map(([name, count]) => `${name}: ${count}`).join(' · ') : 'No proposed assignment violates the checked constraints.'}</p></div></div>
      <div className="live-table-wrap"><table className="live-table"><thead><tr><th>SKU</th><th>Demand</th><th>Current</th><th>Proposed</th><th>Route / pick</th><th>Status</th></tr></thead><tbody>{result.recommendations.map(row => <tr key={row.sku_id} onClick={() => setSelectedSku(row.sku_id)} className={row.sku_id === selectedSku ? 'selected' : ''} tabIndex={0} onKeyDown={event => { if (event.key === 'Enter') setSelectedSku(row.sku_id) }}><td><strong>{row.sku_name}</strong><small>{row.sku_id}</small></td><td>{fmt(row.pick_count)} picks{row.demand_velocity_picks_per_day != null && <small>{fmt(row.demand_velocity_picks_per_day, 2)} / day</small>}</td><td>{row.current_location_id || '—'}</td><td>{row.proposed_location_id || 'No safe slot'}</td><td>{fmt(row.current_round_trip_m, 1)} → {fmt(row.proposed_round_trip_m, 1)} m</td><td><span className={`status-tag ${row.decision}`}>{row.decision}</span></td></tr>)}</tbody></table></div>
      {selected && <div className="live-detail"><strong>{selected.sku_name} · {selected.sku_id}</strong><p>{selected.reason}</p>{selected.constraint_violations.length > 0 && <small>Blocked by: {selected.constraint_violations.join(', ')}</small>}</div>}
      {result.validation.incidents.length > 0 && <details className="live-disclosure"><summary>{result.validation.incidents.length} current-bin safety incident(s)</summary><ul>{result.validation.incidents.slice(0, 30).map((item, i) => <li key={i}>{item.location_id}: {item.detail}</li>)}</ul></details>}
      <details className="live-disclosure"><summary>Method, estimates and data limits</summary><p>Modeled travel score: {change == null ? 'unavailable' : `${fmt(change, 1)}%`}. Optimistic route-only ceiling: {result.metrics.theoretical_travel_score_ceiling_pct == null ? 'unavailable' : `${fmt(result.metrics.theoretical_travel_score_ceiling_pct, 1)}%`}. These are travel estimates, not accuracy. Estimated replenishment: {fmt(result.metrics.estimated_replenishment_cycles)} cycles; current: {fmt(result.metrics.baseline_replenishment_cycles)}.</p><ul>{result.assumptions.map(item => <li key={item}>{item}</li>)}{result.validation.warnings.map((item, i) => <li key={i}>{item.detail}</li>)}</ul></details>
    </>)}
    {open && <div className="live-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !running) setOpen(false) }}><div className="live-modal" role="dialog" aria-modal="true" aria-labelledby="upload-title"><div className="live-modal-head"><div><span className="eyebrow">NEW ANALYSIS</span><h2 id="upload-title">Upload warehouse snapshot</h2></div><button type="button" aria-label="Close upload dialog" onClick={() => setOpen(false)} disabled={running}>×</button></div><form onSubmit={submit}>
      <label>Warehouse name or ID<input list="known-warehouses" value={warehouseName} onChange={event => setWarehouseName(event.target.value)} placeholder="Match the warehouse in your files" required /><datalist id="known-warehouses">{warehouses.map(w => <option key={w.warehouse_id} value={w.name} />)}</datalist></label>
      <label>Exact snapshot date and time<input type="datetime-local" step="1" value={snapshotLocal} onChange={event => setSnapshotLocal(event.target.value)} required /><small>Entered in your computer's time zone and sent to the server in UTC. It must match the timestamp in your manifest and every inventory and slot assignment row. Include seconds when your export has them.</small></label>
      <label>Warehouse data files<input type="file" accept=".csv,.xlsx,.xlsm,.json" multiple onChange={event => setFiles(Array.from(event.target.files || []))} required /><small>Canonical CSV package plus manifest.json, or canonical CSV/Excel tables without a manifest. Missing physical stock, dimensions, policy or aisle data stops optimization.</small></label>
      <details className="upload-help"><summary>Which tables are required?</summary><p>warehouses, zones, locations, skus, sku_profiles, sku_packaging_profiles, inventory_by_location, slot_assignments, aisle_nodes, aisle_edges, location_access, location_hazard_permissions and hazard_segregation_rules. Add orders with order_lines or pick_events for demand. Shared bins need co_storage_rules and shared_bin_plans with items.</p></details>
      {files.length > 0 && <div className="selected-files">{files.length} selected: {files.map(file => file.name).join(', ')}</div>}
      {error && <div className="message error" role="alert">{error}</div>}
      <div className="live-modal-actions"><button type="button" className="button" onClick={() => setOpen(false)} disabled={running}>Cancel</button><button type="submit" className="button primary" disabled={running}>{running ? 'Validating and optimizing…' : 'Process dataset'}</button></div>
      {running && <div className="live-processing" role="status"><strong>Processing your files…</strong><p>Validating stock and policies, calculating picker routes, then evaluating each SKU. Detailed execution logs appear in the FastAPI terminal.</p></div>}
    </form></div></div>}
  </section>
}
