import { useEffect, useState, type FormEvent } from 'react'
import { api, apiUrl, type ImportInspection, type LiveEvaluation, type LiveResult, type Warehouse } from './api'

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
  const [inspection, setInspection] = useState<ImportInspection | null>(null)
  const [inspecting, setInspecting] = useState(false)
  const [useMapping, setUseMapping] = useState(false)
  const [running, setRunning] = useState(false)
  const [processingSeconds, setProcessingSeconds] = useState(0)
  const [error, setError] = useState('')
  const [result, setResult] = useState<LiveResult | null>(null)
  const [evaluation, setEvaluation] = useState<LiveEvaluation | null>(null)
  const [evaluationError, setEvaluationError] = useState('')
  const [evaluating, setEvaluating] = useState(false)
  const [selectedSku, setSelectedSku] = useState<string | null>(null)
  const [resultPage, setResultPage] = useState(1)
  const [decisionFilter, setDecisionFilter] = useState('all')

  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setProcessingSeconds(seconds => seconds + 1), 1000)
    return () => window.clearInterval(timer)
  }, [running])

  async function selectFiles(selected: File[]) {
    setFiles(selected)
    setInspection(null)
    setUseMapping(false)
    setError('')
    const manifest = selected.find(file => file.name.toLowerCase() === 'manifest.json')
    if (!manifest) return
    try {
      const data = JSON.parse(await manifest.text()) as { snapshot_at?: string; warehouse_ids?: string[] }
      if (data.snapshot_at) {
        const date = new Date(data.snapshot_at)
        if (!Number.isNaN(date.getTime())) setSnapshotLocal(localDateTimeInput(date))
      }
      if (data.warehouse_ids?.length && !data.warehouse_ids.some(id => id.toLowerCase() === warehouseName.trim().toLowerCase())) {
        setWarehouseName(data.warehouse_ids[0])
      }
    } catch { /* The server will report invalid manifest content. */ }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    if (!warehouseName.trim() || !snapshotLocal || files.length === 0) {
      setError('Enter the warehouse, snapshot time, and select its data files.')
      return
    }
    setRunning(true)
    setProcessingSeconds(0)
    try {
      const analysis = await api.optimizeUpload(files, warehouseName.trim(), new Date(snapshotLocal).toISOString(), useMapping ? inspection?.sources : undefined)
      setResult(analysis)
      setEvaluation(null)
      setEvaluationError('')
      setSelectedSku(null)
      setResultPage(1)
      setDecisionFilter('all')
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
  async function inspectUpload() {
    setInspecting(true)
    setError('')
    try { setInspection(await api.inspectUpload(files)) }
    catch (issue) { setError((issue as Error).message) }
    finally { setInspecting(false) }
  }
  const change = result?.metrics.optimization_score
  const modeledMetersSaved = result && change != null ? result.metrics.baseline_pick_distance_m * change / 100 : null
  const filtered = result?.recommendations.filter(row => decisionFilter === 'all' || row.decision === decisionFilter) || []
  const pageCount = Math.max(1, Math.ceil(filtered.length / 20))
  const visible = filtered.slice((resultPage - 1) * 20, resultPage * 20)
  const moveCount = result?.recommendations.filter(row => row.decision === 'move').length || 0
  const keepCount = result?.recommendations.filter(row => row.decision === 'keep').length || 0
  const exportUrl = (name: string) => result ? apiUrl(`/live/export/${result.analysis_id}/${name}`) : '#'
  async function submitEvaluation(file: File) {
    if (!result) return
    setEvaluating(true)
    setEvaluationError('')
    try { setEvaluation(await api.evaluateLive(result.analysis_id, file)) }
    catch (issue) { setEvaluationError((issue as Error).message) }
    finally { setEvaluating(false) }
  }
  return <section className="live-workspace" id="live">
    <div className="workflow-steps"><span className={open && !running ? 'active' : ''}>1 · Upload data</span><span className={running ? 'active' : ''}>2 · Process dataset</span><span className={!open && result ? 'active' : ''}>3 · Review results</span></div>
    <div className="live-header">
      <div><span className="eyebrow">YOUR WAREHOUSE DATA</span><h2>Optimize an uploaded snapshot</h2><p>Upload warehouse files, wait for the checks and route calculations, then review each proposed move. Stock is never moved automatically.</p></div>
      <button className="button primary" onClick={() => { setError(''); setOpen(true) }}>{result ? 'Upload another dataset' : 'Upload warehouse data'}</button>
    </div>
    {!open && (!result ? <div className="live-empty"><strong>No uploaded analysis yet</strong><span>Start with warehouse, zone, bin, SKU, package, bin inventory, assignment, aisle layout and demand files. CSV and XLSX/XLSM are accepted. A canonical manifest is recommended.</span></div> : <>
      <div className="live-meta"><strong>{result.warehouse_name}</strong><span>Snapshot {new Date(result.snapshot_at).toLocaleString()}</span><span>{result.demand_source}</span><span>{result.statistics.inventory_rows} stock rows · {result.statistics.candidate_locations} candidate bins</span></div>
      <div className="results-intro"><div><span className="eyebrow">PLACEMENT PLAN</span><h2>Where each SKU should go</h2><p>Each proposed bin is a recommendation for review. Open a row to see the reason; use the stock movements download for exact source bins, packages, lots and quantities.</p></div><div className="result-downloads"><a className="button primary" href={exportUrl('results.xlsx')}>Download Excel plan</a><a className="button" href={exportUrl('placements.csv')}>Placements CSV</a><a className="button" href={exportUrl('movements.csv')}>Stock movements CSV</a></div></div>
      <div className="results-summary"><div><strong>{moveCount}</strong><span>Move to a proposed bin</span></div><div><strong>{keepCount}</strong><span>Keep in current bin</span></div><div><strong>{result.metrics.unassigned_skus}</strong><span>Need manual review</span></div><div><strong>{result.movement_lines.length}</strong><span>Stock movement lines</span></div></div>
      {result.validation.warnings.filter(warning => ['post_snapshot_demand', 'future_snapshot_preview'].includes(warning.code)).map((warning, index) => <div className="live-data-warning" role="status" key={`${warning.code}-${index}`}>{warning.detail}</div>)}
      {result.validation.warnings.some(warning => warning.code === 'unsafe_pack_capacity') && <details className="live-data-warning"><summary>{result.validation.warnings.filter(warning => warning.code === 'unsafe_pack_capacity').length} unsafe capacity certificate(s) excluded · review before operational use</summary><ul>{result.validation.warnings.filter(warning => warning.code === 'unsafe_pack_capacity').map((warning, index) => <li key={index}>{warning.detail}</li>)}</ul></details>}
      <div className="live-kpis">
        <div><span>Estimated pick travel saved</span><strong className={modeledMetersSaved != null && modeledMetersSaved < 0 ? 'caution' : 'good'}>{modeledMetersSaved == null ? '—' : `${fmt(modeledMetersSaved, 0)} m`}</strong><small>Modeled against the current layout; unassigned picks receive no savings credit</small></div>
        <div><span>Assigned SKUs</span><strong>{fmt(result.metrics.assigned_skus)}</strong><small>{fmt(result.metrics.unassigned_skus)} unassigned · {fmt(result.metrics.unserved_pick_count)} picks affected</small></div>
        <div><span>Proposed utilization</span><strong>{result.metrics.proposed_utilization_pct == null ? '—' : `${fmt(result.metrics.proposed_utilization_pct, 1)}%`}</strong><small>{fmt(result.metrics.baseline_utilization_pct, 1)}% current · candidate bins only</small></div>
        <div><span>Observed placement agreement</span><strong>{evaluation ? `${fmt(evaluation.placement_agreement_pct, 1)}%` : 'Not measured'}</strong><small>{evaluation ? `${evaluation.observed_skus} of ${evaluation.proposed_skus} SKUs observed; not an optimality score` : 'Upload post-placement outcomes below'}</small></div>
      </div>
      <section className="live-evaluation"><div><span className="eyebrow">OBSERVED EVALUATION</span><h3>Check results against warehouse outcomes</h3><p>After staff review and place stock, upload an independent CSV with <code>sku_id,actual_location_id,observed_at</code>. Add <code>measured_round_trip_m</code> to compare modeled and measured travel. Observed timestamps must be after the snapshot.</p></div><label className="button" htmlFor="outcome-file">{evaluating ? 'Evaluating…' : 'Upload outcome CSV'}</label><input id="outcome-file" type="file" accept=".csv" disabled={evaluating} onChange={event => { const file = event.target.files?.[0]; if (file) void submitEvaluation(file); event.target.value = '' }} />{evaluation && <div className="evaluation-metrics"><span>Coverage <b>{fmt(evaluation.coverage_pct, 1)}%</b></span><span>Move agreement <b>{evaluation.move_agreement_pct == null ? '—' : `${fmt(evaluation.move_agreement_pct, 1)}%`}</b></span><span>Measured route error <b>{evaluation.route_mae_m == null ? '—' : `${fmt(evaluation.route_mae_m, 1)} m`}</b></span><small>{evaluation.note}</small></div>}{evaluationError && <p className="message error" role="alert">{evaluationError}</p>}</section>
      <div className="live-insights"><div><strong>Modeled picking distance</strong><p>{fmt(result.metrics.comparable_baseline_m, 1)} m current → {fmt(result.metrics.comparable_proposed_m, 1)} m proposed on comparable SKUs.</p></div><div><strong>Why some candidate bins were rejected</strong><p>{Object.keys(result.metrics.constraint_violations).length ? Object.entries(result.metrics.constraint_violations).map(([name, count]) => `${name}: ${count}`).join(' · ') : 'No proposed assignment violates the checked constraints.'}</p></div></div>
      <section className="placement-section"><div className="placement-head"><div><span className="eyebrow">SKU PLACEMENTS</span><h3>Review the proposed locations</h3><p>Showing {filtered.length ? (resultPage - 1) * 20 + 1 : 0}–{Math.min(resultPage * 20, filtered.length)} of {filtered.length} SKUs</p></div><div className="placement-filters" aria-label="Filter placement decisions">{[['all','All'],['move','Moves'],['keep','Keep'],['unassigned','Unassigned']].map(([value,label]) => <button key={value} type="button" className={decisionFilter === value ? 'active' : ''} onClick={() => { setDecisionFilter(value); setResultPage(1); setSelectedSku(null) }}>{label}</button>)}</div></div>
      <div className="live-table-wrap"><table className="live-table"><thead><tr><th>SKU</th><th>Pick demand</th><th>Current bin</th><th>Place in</th><th>Route / pick</th><th>Decision</th></tr></thead><tbody>{visible.map(row => <tr key={row.sku_id} onClick={() => setSelectedSku(row.sku_id)} className={row.sku_id === selectedSku ? 'selected' : ''} tabIndex={0} onKeyDown={event => { if (event.key === 'Enter') setSelectedSku(row.sku_id) }}><td><strong>{row.sku_name}</strong><small>{row.sku_id}</small></td><td>{fmt(row.pick_count)} picks{row.demand_velocity_picks_per_day != null && <small>{fmt(row.demand_velocity_picks_per_day, 2)} / day</small>}</td><td>{row.current_location_id || '—'}</td><td><strong className={row.proposed_location_id ? 'proposed-bin' : 'missing-bin'}>{row.proposed_location_id || 'No safe bin'}</strong></td><td>{fmt(row.current_round_trip_m, 1)} → {fmt(row.proposed_round_trip_m, 1)} m</td><td><span className={`status-tag ${row.decision}`}>{row.decision}</span></td></tr>)}</tbody></table></div>
      {pageCount > 1 && <div className="placement-pages"><button type="button" disabled={resultPage === 1} onClick={() => { setResultPage(resultPage - 1); setSelectedSku(null) }}>Previous 20</button><span>Page {resultPage} of {pageCount}</span><button type="button" disabled={resultPage === pageCount} onClick={() => { setResultPage(resultPage + 1); setSelectedSku(null) }}>Next 20</button></div>}
      {selected && <div className="live-detail"><span className="eyebrow">PLACEMENT EXPLANATION</span><strong>{selected.sku_name} · {selected.sku_id}</strong><p><b>{selected.current_location_id || 'Unknown current bin'} → {selected.proposed_location_id || 'No safe bin assigned'}</b></p><p>{selected.reason}</p>{selected.constraint_violations.length > 0 && <small>Blocked by: {selected.constraint_violations.join(', ')}</small>}{result.movement_lines.filter(line => line.sku_id === selected.sku_id).map((line, i) => <p key={i}>Move {line.handling_unit_count} pack(s) · {fmt(line.on_hand_qty, 2)} {line.base_uom} from {line.source_location_id} to {line.proposed_location_id}{line.lot_id ? ` · lot ${line.lot_id}` : ''}</p>)}</div>}
      </section>
      {result.validation.incidents.length > 0 && <details className="live-disclosure"><summary>{result.validation.incidents.length} current-bin safety incident(s)</summary><ul>{result.validation.incidents.slice(0, 30).map((item, i) => <li key={i}>{item.location_id}: {item.detail}</li>)}</ul></details>}
      <details className="live-disclosure"><summary>Method, estimates and data limits</summary><p>Modeled travel score: {change == null ? 'unavailable' : `${fmt(change, 1)}%`}. Optimistic route-only ceiling: {result.metrics.theoretical_travel_score_ceiling_pct == null ? 'unavailable' : `${fmt(result.metrics.theoretical_travel_score_ceiling_pct, 1)}%`}. These are travel estimates, not accuracy. Estimated replenishment: {fmt(result.metrics.estimated_replenishment_cycles)} cycles; current: {fmt(result.metrics.baseline_replenishment_cycles)}.</p><ul>{result.assumptions.map(item => <li key={item}>{item}</li>)}{result.validation.warnings.map((item, i) => <li key={i}>{item.detail}</li>)}</ul></details>
    </>)}
    {open && <div className="live-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !running) setOpen(false) }}><div className="live-modal" role="dialog" aria-modal="true" aria-labelledby="upload-title"><div className="live-modal-head"><div><span className="eyebrow">NEW ANALYSIS</span><h2 id="upload-title">Upload warehouse snapshot</h2></div><button type="button" aria-label="Close upload dialog" onClick={() => setOpen(false)} disabled={running}>×</button></div><form onSubmit={submit}>
      <label>Warehouse name or ID<input list="known-warehouses" value={warehouseName} onChange={event => setWarehouseName(event.target.value)} placeholder="Match the warehouse in your files" required /><datalist id="known-warehouses">{warehouses.map(w => <option key={w.warehouse_id} value={w.name} />)}</datalist></label>
      <label>Exact snapshot date and time<input type="datetime-local" step="1" value={snapshotLocal} onChange={event => setSnapshotLocal(event.target.value)} required /><small>Entered in your computer's time zone and sent to the server in UTC. It must match the timestamp in your manifest and every inventory and slot assignment row. Include seconds when your export has them.</small></label>
      <label>Warehouse data files<input type="file" accept=".csv,.xlsx,.xlsm,.json" multiple onChange={event => void selectFiles(Array.from(event.target.files || []))} required /><small>Upload CSV or Excel files with your own column names, then choose “Inspect data and map columns”. You can inspect files before entering a warehouse or snapshot. Canonical packages also work directly. Safe placement recommendations require warehouse measurements, stock, policies and routes.</small></label>
      <details className="upload-help"><summary>Which tables are required?</summary><p>warehouses, zones, locations, skus, sku_profiles, sku_packaging_profiles, inventory_by_location, slot_assignments, aisle_nodes, aisle_edges, location_access, location_hazard_permissions and hazard_segregation_rules. Add orders with order_lines or pick_events for demand. Shared bins need co_storage_rules and shared_bin_plans with items.</p></details>
      {files.length > 0 && <div className="selected-files">{files.length} selected: {files.map(file => file.name).join(', ')}</div>}
      {files.length > 0 && <button type="button" className="button" disabled={inspecting || running} onClick={() => void inspectUpload()}>{inspecting ? 'Inspecting files…' : 'Inspect data and map columns'}</button>}
      {inspection && <section className="import-inspection"><h3>Dataset inspection</h3><p>{inspection.note}</p>{!files.some(file => file.name.toLowerCase() === 'manifest.json') && <label><input type="checkbox" checked={useMapping} onChange={event => setUseMapping(event.target.checked)} />Use these column mappings when processing</label>}{inspection.sources.map((source, index) => <details key={source.source}><summary>{source.source} · {fmt(source.rows)} rows · {source.columns.length} columns · {source.blank_cells} blank cells</summary><label>Canonical table<select value={source.table} onChange={event => setInspection({ ...inspection, sources: inspection.sources.map((item, i) => i === index ? { ...item, table: event.target.value, mapping: {} } : item) })}><option value="">Analysis only / ignore for placements</option>{Object.keys(inspection.schemas).map(table => <option key={table}>{table}</option>)}</select></label><div className="import-columns">{source.columns.map(column => <label key={column}>{column}<select aria-label={`Map ${source.source} ${column}`} value={source.mapping[column] || ''} onChange={event => setInspection({ ...inspection, sources: inspection.sources.map((item, i) => i === index ? { ...item, mapping: { ...item.mapping, [column]: event.target.value } } : item) })}><option value="">Leave unmapped</option>{(inspection.schemas[source.table] || []).map(field => <option key={field.name} value={field.name}>{field.name} ({field.type}){field.required ? ' *' : ''}</option>)}</select></label>)}</div><p>Required fields without a mapping: {(inspection.schemas[source.table] || []).filter(field => field.required && !Object.values(source.mapping).includes(field.name)).map(field => field.name).join(', ') || 'None'}</p><details><summary>Preview first three rows</summary><pre>{JSON.stringify(source.sample, null, 2)}</pre></details></details>)}<p>Warehouse tables still needed: {inspection.required_tables.filter(table => !inspection.sources.some(source => source.table === table)).join(', ') || 'All required table types detected; row validation runs during processing.'}</p></section>}
      {error && <div className="message error" role="alert">{error}</div>}
      <div className="live-modal-actions"><button type="button" className="button" onClick={() => setOpen(false)} disabled={running}>Cancel</button><button type="submit" className="button primary" disabled={running}>{running ? 'Validating and optimizing…' : 'Process dataset'}</button></div>
      {running && <div className="live-processing" role="status"><strong>Processing your files… {processingSeconds}s elapsed</strong><p>The request is still running. Large multi-warehouse packages can take tens of seconds to validate and optimize. Detailed execution logs appear in the FastAPI terminal.</p></div>}
    </form></div></div>}
  </section>
}
