import { useEffect, useState } from 'react'
import { api, apiUrl } from './api'
import type { ExceptionCase, Forecast, Overview, Recommendation, RecommendationPage, Validation, Warehouse } from './api'

const PAGE_SIZE = 12
const fmt = (value: number, digits = 0) => value.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits })
const title = (value: string) => value.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase())

function Placeholder({ message }: { message: string }) {
  return <div className="placeholder">{message}</div>
}

function StatCard({ label, value, detail, tone }: { label: string; value: string; detail: string; tone?: 'good' | 'caution' }) {
  return <div className="stat-card"><span className="stat-label">{label}</span><strong className={tone ? `stat-value ${tone}` : 'stat-value'}>{value}</strong><span className="stat-detail">{detail}</span></div>
}

function ComparisonRow({ label, baseline, proposed, unit, changeUnit, digits = 0, direction }: {
  label: string; baseline: number; proposed: number; unit: string; changeUnit?: string; digits?: number; direction: 'lower' | 'higher' | 'neutral'
}) {
  const max = Math.max(baseline, proposed, 1)
  const difference = proposed - baseline
  const improved = direction === 'higher' ? difference > 0 : direction === 'lower' ? difference < 0 : false
  const worsened = direction === 'higher' ? difference < 0 : direction === 'lower' ? difference > 0 : false
  return <div className="comparison-row">
    <div className="comparison-top"><strong>{label}</strong><span className={worsened ? 'change caution' : improved ? 'change good' : 'change'}>{difference === 0 ? 'No change' : `${difference > 0 ? '+' : '−'}${fmt(Math.abs(difference), digits)} ${changeUnit || unit}`}</span></div>
    <div className="bar-line"><span>Current</span><div className="bar-track"><div className="bar-fill baseline" style={{ width: `${baseline / max * 100}%` }} /></div><b>{fmt(baseline, digits)} {unit}</b></div>
    <div className="bar-line"><span>Proposed</span><div className="bar-track"><div className="bar-fill proposed" style={{ width: `${proposed / max * 100}%` }} /></div><b>{fmt(proposed, digits)} {unit}</b></div>
  </div>
}

function RecommendationDetail({ row }: { row: Recommendation }) {
  return <div className="detail-card">
    <div className="detail-heading"><div><span className="eyebrow">DECISION DETAIL</span><h3>{row.sku_name}</h3><p className="mono">{row.sku_id}</p></div><span className={`status-tag ${row.decision}`}>{title(row.decision)}</span></div>
    <p className="detail-intro">{row.reason || 'No explanation available for this recommendation.'}</p>
    <div className="detail-grid"><div><span>Current location</span><strong className="mono">{row.baseline_location_id || '—'}</strong></div><div><span>Proposed location</span><strong className="mono">{row.location_id || 'No eligible slot'}</strong></div><div><span>Current route</span><strong>{row.baseline_distance_m ? `${fmt(Number(row.baseline_distance_m))} m` : '—'}</strong></div><div><span>Proposed route</span><strong>{row.optimized_distance_m ? `${fmt(Number(row.optimized_distance_m))} m` : '—'}</strong></div><div><span>Order-line picks</span><strong>{fmt(Number(row.pick_frequency))}</strong></div><div><span>Score</span><strong>{row.score || '—'}</strong></div></div>
    <div className="constraint-list"><span className="constraint">Category: {title(row.category)}</span><span className="constraint">Temperature: {title(row.temperature)}</span><span className="constraint">Hazard: {title(row.hazard_class)}</span></div>
    <p className="note">This proposal has not moved stock. Review location availability before applying it operationally.</p>
  </div>
}

export default function Dashboard() {
  const [warehouses, setWarehouses] = useState<Warehouse[]>([])
  const [warehouseId, setWarehouseId] = useState('')
  const [overview, setOverview] = useState<Overview | null>(null)
  const [validation, setValidation] = useState<Validation | null>(null)
  const [exceptions, setExceptions] = useState<ExceptionCase[]>([])
  const [recommendations, setRecommendations] = useState<RecommendationPage | null>(null)
  const [selectedSku, setSelectedSku] = useState<string | null>(null)
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('impact')
  const [page, setPage] = useState(0)
  const [forecast, setForecast] = useState<Forecast | null>(null)
  const [loading, setLoading] = useState(true)
  const [recommendationsLoading, setRecommendationsLoading] = useState(true)
  const [forecastLoading, setForecastLoading] = useState(false)
  const [optimizing, setOptimizing] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    const timer = window.setTimeout(() => { setSearch(searchInput.trim()); setPage(0) }, 250)
    return () => window.clearTimeout(timer)
  }, [searchInput])

  useEffect(() => {
    let active = true
    Promise.all([api.warehouses(), api.exceptions(), api.validation()])
      .then(([warehouseRows, exceptionRows, validationResult]) => {
        if (!active) return
        setWarehouses(warehouseRows)
        setExceptions(exceptionRows.items)
        setValidation(validationResult)
      })
      .catch(err => { if (active) setError((err as Error).message) })
    return () => { active = false }
  }, [revision])

  useEffect(() => {
    let active = true
    setLoading(true)
    api.overview(warehouseId)
      .then(result => { if (active) { setOverview(result); setError('') } })
      .catch(err => { if (active) { setOverview(null); setError((err as Error).message) } })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [warehouseId, revision])

  useEffect(() => {
    let active = true
    setRecommendationsLoading(true)
    api.recommendations({ warehouseId, search, sort, offset: page * PAGE_SIZE, limit: PAGE_SIZE })
      .then(result => { if (active) { setRecommendations(result); setSelectedSku(null) } })
      .catch(err => { if (active) { setRecommendations(null); setError((err as Error).message) } })
      .finally(() => { if (active) setRecommendationsLoading(false) })
    return () => { active = false }
  }, [warehouseId, search, sort, page, revision])

  async function runOptimization() {
    setOptimizing(true)
    setNotice('')
    try {
      const result = await api.optimize()
      setNotice(`Optimization run ${result.run_id} completed. Recommendations are proposals for review.`)
      setRevision(value => value + 1)
    } catch (err) { setError((err as Error).message) }
    finally { setOptimizing(false) }
  }

  async function runForecast() {
    setForecastLoading(true)
    try { setForecast(await api.forecast()) }
    catch (err) { setError((err as Error).message) }
    finally { setForecastLoading(false) }
  }

  const run = overview?.metrics
  const current = run?.baseline
  const proposed = run?.optimized
  const timeDifference = current && proposed ? current.picking_time_hours - proposed.picking_time_hours : null
  const refillDifference = current && proposed ? proposed.replenishment_tasks - current.replenishment_tasks : null
  const selectedRow = recommendations?.items.find(item => item.sku_id === selectedSku) || null
  const pageCount = recommendations ? Math.ceil(recommendations.total / PAGE_SIZE) : 0
  const verified = validation ? `${fmt(validation.exception_cases_correct)} / ${fmt(validation.exception_cases_detected)}` : '—'

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#overview"><span className="brand-symbol">S</span><span><strong>Slotwise</strong><small>Warehouse slotting</small></span></a>
      <div className="sidebar-section">WORKSPACE</div>
      <nav aria-label="Main navigation"><a href="#overview">Overview</a><a href="#impact">Operational impact</a><a href="#recommendations">Recommendations</a><a href="#exceptions">Exceptions</a><a href="#data">Data & model</a></nav>
      <div className="sidebar-meta"><span className="status-dot" />{overview ? `${title(overview.dataset.scale)} dataset` : 'Dataset unavailable'}<small>{overview ? `Seed ${overview.dataset.seed}` : 'Connect the API to continue'}</small></div>
    </aside>

    <main className="main">
      <header className="topbar"><div><span className="topbar-kicker">OPERATIONS</span><strong>Slotting workspace</strong></div><div className="topbar-actions"><label className="select-label" htmlFor="warehouse-select">Warehouse</label><select id="warehouse-select" value={warehouseId} onChange={event => { setWarehouseId(event.target.value); setPage(0) }}><option value="">All warehouses</option>{warehouses.map(warehouse => <option key={warehouse.warehouse_id} value={warehouse.warehouse_id}>{warehouse.name}</option>)}</select><span className={overview ? 'connection connected' : 'connection'}><span className="status-dot" />{overview ? 'API connected' : 'Waiting for API'}</span></div></header>

      <div className="content">
        {error && <div className="message error" role="alert"><strong>Data could not be loaded.</strong><span>{error}</span><button onClick={() => setRevision(value => value + 1)}>Retry</button></div>}
        {notice && <div className="message success" role="status">{notice}<button aria-label="Dismiss message" onClick={() => setNotice('')}>×</button></div>}

        <section className="page-intro" id="overview"><div><span className="eyebrow">WAREHOUSE DECISION SUPPORT</span><h1>Put every SKU in a better place.</h1><p>Compare the current layout with a constraint-checked proposal using the same orders and warehouse geometry.</p><div className="run-meta">{run ? <><span>Run <code>{run.run_id}</code></span><span>Scope: {overview?.scope.name}</span><span>Source: {overview?.dataset.source}</span></> : <span>{loading ? 'Loading the latest run…' : 'No run data available'}</span>}</div></div><div className="intro-actions"><button className="button primary" onClick={runOptimization} disabled={optimizing || !overview}>{optimizing ? 'Running optimization…' : 'Run optimization'}</button>{overview && <a className="button outline" href={apiUrl('/export/slot_assignments')}>Download current slots</a>}</div></section>

        <section className="section" aria-label="Key metrics"><div className="section-title"><div><span className="eyebrow">LATEST SIMULATION</span><h2>What changes with this proposal</h2></div><p>Estimates from synthetic data, not measured warehouse savings.</p></div><div className="stats-grid">
          <StatCard label="Picking distance" value={current && proposed ? `${fmt(Math.abs(current.picking_distance_m - proposed.picking_distance_m) / 1000, 1)} km ${proposed.picking_distance_m <= current.picking_distance_m ? 'less' : 'more'}` : '—'} detail={current && proposed ? `${fmt(current.picking_distance_m / 1000, 1)} → ${fmt(proposed.picking_distance_m / 1000, 1)} km of travel` : 'Awaiting simulation data'} tone={current && proposed ? proposed.picking_distance_m <= current.picking_distance_m ? 'good' : 'caution' : undefined} />
          <StatCard label="Picking time" value={timeDifference === null ? '—' : `${fmt(Math.abs(timeDifference), 1)} h ${timeDifference >= 0 ? 'saved' : 'added'}`} detail={current && proposed ? `${fmt(current.picking_time_hours, 1)} → ${fmt(proposed.picking_time_hours, 1)} hours` : 'Awaiting simulation data'} tone={timeDifference === null ? undefined : timeDifference >= 0 ? 'good' : 'caution'} />
          <StatCard label="Replenishment impact" value={refillDifference === null ? '—' : `${refillDifference > 0 ? '+' : refillDifference < 0 ? '−' : ''}${fmt(Math.abs(refillDifference))} tasks`} detail={current && proposed ? `${fmt(current.replenishment_tasks)} → ${fmt(proposed.replenishment_tasks)} simulated tasks` : 'Awaiting simulation data'} tone={refillDifference === null ? undefined : refillDifference > 0 ? 'caution' : 'good'} />
          <StatCard label="SKUs assigned" value={proposed ? fmt(proposed.assigned_skus) : '—'} detail={proposed ? `${fmt(proposed.unassigned_skus)} without a compatible proposed slot` : 'Awaiting simulation data'} tone={proposed && proposed.unassigned_skus === 0 ? 'good' : undefined} />
        </div></section>

        <section className="impact-grid" id="impact"><div className="panel impact-panel"><div className="panel-heading"><div><span className="eyebrow">OPERATIONAL IMPACT</span><h2>Current vs proposed</h2></div><span className="muted">Identical order workload</span></div>{current && proposed ? <div className="comparison-list"><ComparisonRow label="Pick travel" baseline={current.picking_distance_m / 1000} proposed={proposed.picking_distance_m / 1000} unit="km" digits={1} direction="lower" /><ComparisonRow label="Pick time" baseline={current.picking_time_hours} proposed={proposed.picking_time_hours} unit="h" digits={1} direction="lower" /><ComparisonRow label="Replenishment" baseline={current.replenishment_tasks} proposed={proposed.replenishment_tasks} unit="tasks" direction="lower" /><ComparisonRow label="Throughput" baseline={current.throughput_lines_per_hour} proposed={proposed.throughput_lines_per_hour} unit="lines/h" digits={1} direction="higher" /><ComparisonRow label="Space utilization proxy" baseline={current.space_utilization_pct} proposed={proposed.space_utilization_pct} unit="%" changeUnit="pp" digits={1} direction="neutral" /></div> : <Placeholder message={loading ? 'Loading comparison data…' : 'No comparison data is available.'} />}</div>
          <div className="panel takeaways"><span className="eyebrow">MANAGER TAKEAWAYS</span><h2>Read the tradeoffs</h2>{current && proposed ? <><div className="takeaway"><span className="takeaway-icon positive">↘</span><div><strong>{fmt(Math.abs(run?.distance_reduction_pct ?? 0), 1)}% {run && run.distance_reduction_pct >= 0 ? 'less' : 'more'} pick travel</strong><p>Demand-heavy SKUs are placed nearer to the picking area when a compatible location is available.</p></div></div><div className="takeaway"><span className={`takeaway-icon ${refillDifference !== null && refillDifference > 0 ? 'warning' : 'positive'}`}>↔</span><div><strong>{refillDifference === null ? 'Replenishment unavailable' : refillDifference > 0 ? `${fmt(refillDifference)} additional refill tasks` : refillDifference < 0 ? `${fmt(Math.abs(refillDifference))} fewer refill tasks` : 'No refill task change'}</strong><p>Review labor impact alongside travel savings. The algorithm currently prioritizes pick distance.</p></div></div><div className="takeaway"><span className="takeaway-icon neutral">✓</span><div><strong>{fmt(proposed.pick_lines)} order lines compared</strong><p>Both layouts use the same generated orders, routes, and handling assumptions.</p></div></div></> : <Placeholder message="Takeaways will appear when simulation results load." />}<div className="review-note">Recommendations are proposals. No stock is moved automatically.</div></div></section>

        <section className="panel recommendations-panel" id="recommendations"><div className="panel-heading"><div><span className="eyebrow">SLOTTING DECISIONS</span><h2>Review recommendations</h2><p>Search a SKU, compare its route, and inspect the reason behind its proposed slot.</p></div><span className="record-count">{recommendations ? `${fmt(recommendations.total)} ${recommendations.total === 1 ? 'SKU' : 'SKUs'}` : '— SKUs'}</span></div><div className="table-toolbar"><label className="search-box"><span>⌕</span><input value={searchInput} onChange={event => setSearchInput(event.target.value)} placeholder="Search SKU ID or product name" aria-label="Search recommendations" /></label><label className="sort-box">Sort by <select value={sort} onChange={event => { setSort(event.target.value); setPage(0) }}><option value="impact">Estimated travel impact</option><option value="frequency">Pick frequency</option><option value="sku">SKU ID</option></select></label></div><div className="table-wrap"><table><thead><tr><th>Product</th><th>Current slot</th><th>Proposed slot</th><th>Pick lines</th><th>Route change / pick</th><th>Decision</th></tr></thead><tbody>{recommendations?.items.map(row => <tr key={row.sku_id} className={selectedSku === row.sku_id ? 'selected-row' : ''} onClick={() => setSelectedSku(row.sku_id)} tabIndex={0} onKeyDown={event => { if (event.key === 'Enter') setSelectedSku(row.sku_id) }}><td><strong>{row.sku_name}</strong><small className="mono">{row.sku_id}</small></td><td className="mono">{row.baseline_location_id || '—'}</td><td className="mono location">{row.location_id || 'No slot'}</td><td>{fmt(Number(row.pick_frequency))}</td><td className={row.distance_saving_m === null ? '' : row.distance_saving_m < 0 ? 'caution' : row.distance_saving_m > 0 ? 'good' : ''}>{row.distance_saving_m === null ? '—' : row.distance_saving_m > 0 ? `${fmt(row.distance_saving_m)} m shorter` : row.distance_saving_m < 0 ? `${fmt(Math.abs(row.distance_saving_m))} m longer` : 'No change'}</td><td><span className={`status-tag ${row.decision}`}>{title(row.decision)}</span></td></tr>)}</tbody></table>{!recommendationsLoading && recommendations?.items.length === 0 && <Placeholder message={search ? 'No SKUs match this search. Try another SKU ID or product name.' : 'No recommendations are available for this scope.'} />}{recommendationsLoading && <Placeholder message="Loading recommendations…" />}</div><div className="table-footer"><span>{recommendations ? `Showing ${recommendations.total ? fmt(page * PAGE_SIZE + 1) : 0}–${fmt(Math.min((page + 1) * PAGE_SIZE, recommendations.total))} of ${fmt(recommendations.total)}` : 'No rows loaded'}</span><div><button disabled={page === 0} onClick={() => setPage(value => value - 1)}>Previous</button><span>{pageCount ? `Page ${page + 1} of ${pageCount}` : 'No pages'}</span><button disabled={!pageCount || page + 1 >= pageCount} onClick={() => setPage(value => value + 1)}>Next</button></div></div>{selectedRow && <RecommendationDetail row={selectedRow} />}</section>

        <div className="bottom-grid"><section className="panel" id="exceptions"><div className="panel-heading"><div><span className="eyebrow">EXCEPTION FIXTURES</span><h2>Known problem scenarios</h2><p>Deliberate cases used to verify the rules. These are not live incidents.</p></div><span className="record-count">{verified} matched</span></div>{exceptions.length ? <div className="exception-list">{exceptions.map(item => <div className="exception-item" key={item.case_id}><span className={`exception-icon ${item.correct ? 'matched' : 'unmatched'}`}>{item.correct ? '✓' : '!'}</span><div><strong>{title(item.detected_issue)}</strong><small>{item.case_id} · Expected {title(item.expected_issue)}</small></div><span className={`status-tag ${item.correct ? 'verified' : 'unassigned'}`}>{item.correct ? 'Matched' : 'Mismatch'}</span></div>)}</div> : <Placeholder message="Exception fixtures will appear when the API is available." />}</section>
          <section className="panel" id="data"><div className="panel-heading"><div><span className="eyebrow">DATA & VALIDATION</span><h2>Know what the model used</h2></div></div><div className="data-grid"><div><span>Dataset scale</span><strong>{overview ? title(overview.dataset.scale) : '—'}</strong></div><div><span>Fixed seed</span><strong>{overview ? String(overview.dataset.seed) : '—'}</strong></div><div><span>Warehouses</span><strong>{overview ? fmt(overview.dataset.rows.warehouses ?? 0) : '—'}</strong></div><div><span>Locations</span><strong>{overview ? fmt(overview.dataset.rows.locations ?? 0) : '—'}</strong></div><div><span>SKUs</span><strong>{overview ? fmt(overview.dataset.rows.skus ?? 0) : '—'}</strong></div><div><span>Order lines</span><strong>{overview ? fmt(overview.dataset.rows.order_lines ?? 0) : '—'}</strong></div></div><div className={`validation-strip ${validation?.valid ? 'passed' : validation ? 'failed' : ''}`}><span>{validation ? validation.valid ? '✓ Integrity checks passed' : `${validation.errors.length} integrity issue(s) found` : 'Validation status unavailable'}</span><span>{validation ? `${fmt(validation.exception_cases_correct)} / ${fmt(validation.exception_cases_detected)} exception labels matched` : '—'}</span></div>{validation && !validation.valid && <ul className="validation-errors">{validation.errors.slice(0, 5).map((issue, index) => <li key={`${issue}-${index}`}>{issue}</li>)}</ul>}{overview && <div className="data-links"><a href={apiUrl('/export/slot_assignments')}>Current assignments CSV ↗</a><a href={apiUrl('/export/ground_truth')}>Ground truth CSV ↗</a></div>}</section></div>

        <section className="panel forecast-panel" id="forecast"><div className="panel-heading"><div><span className="eyebrow">OPTIONAL MACHINE LEARNING</span><h2>Demand forecast</h2><p>Train on historical picks and compare holdout error with a simple rolling average. Forecasts do not override slot constraints.</p></div><button className="button subtle" onClick={runForecast} disabled={forecastLoading || !overview}>{forecastLoading ? 'Training model…' : forecast ? 'Run again' : 'Run forecast'}</button></div>{forecast ? <div className="forecast-content"><div className="forecast-scores"><div><span>Model holdout error</span><strong>{fmt(forecast.holdout_mae, 2)}</strong><small>picks / SKU / day</small></div><div><span>Rolling average error</span><strong>{fmt(forecast.rolling_mean_baseline_mae, 2)}</strong><small>same holdout period</small></div><p>{forecast.model} · {fmt(forecast.training_days)} training days · {fmt(forecast.holdout_days)} holdout days</p></div><div className="forecast-table"><div className="forecast-table-head"><span>SKU</span><span>Last 7 days</span><span>Next 7 days</span></div>{forecast.forecast.slice(0, 5).map(row => <div className="forecast-table-row" key={row.sku_id}><strong className="mono">{row.sku_id}</strong><span>{fmt(row.historical_7_day_picks, 1)} picks</span><span>{fmt(row.forecast_7_day_picks, 1)} picks</span></div>)}</div></div> : <Placeholder message={forecastLoading ? 'Training the demand model…' : 'No forecast has been run. Use “Run forecast” to see model results.'} />}</section>

        <footer>{overview?.dataset.source || 'Data source unavailable'} · Simulated operational estimates · Review proposals before making physical changes</footer>
      </div>
    </main>
  </div>
}
