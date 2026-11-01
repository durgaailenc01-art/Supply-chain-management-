export type Metrics = {
  assigned_skus: number
  unassigned_skus: number
  pick_lines: number
  picking_distance_m: number
  picking_time_hours: number
  throughput_lines_per_hour: number
  replenishment_tasks: number
  space_utilization_pct: number
}

export type Overview = {
  metrics: {
    run_id: string
    dataset: string
    baseline: Metrics
    optimized: Metrics
    distance_reduction_pct: number
  }
  scope: { warehouse_id: string | null; name: string }
  dataset: { scale: string; seed: number; source: string; rows: Record<string, number> }
}

export type Warehouse = { warehouse_id: string; name: string; status: string }

export type Recommendation = {
  sku_id: string
  sku_name: string
  category: string
  warehouse_id: string
  temperature: string
  hazard_class: string
  baseline_location_id: string
  location_id: string
  baseline_distance_m: string
  optimized_distance_m: string
  distance_saving_m: number | null
  weighted_distance_saving_m: number | null
  pick_frequency: string
  score: string
  reason: string
  decision: 'move' | 'keep' | 'unassigned'
}

export type RecommendationPage = {
  total: number
  limit: number
  offset: number
  items: Recommendation[]
}

export type ExceptionCase = {
  case_id: string
  detected_issue: string
  expected_issue: string
  expected_decision: string
  correct: boolean
}

export type Validation = {
  valid: boolean
  errors: string[]
  row_counts: Record<string, number>
  exception_cases_detected: number
  exception_cases_correct: number
}

export type Forecast = {
  model: string
  training_days: number
  holdout_days: number
  holdout_mae: number
  rolling_mean_baseline_mae: number
  forecast: {
    sku_id: string
    historical_7_day_picks: number
    forecast_7_day_picks: number
  }[]
}

export type LiveResult = {
  analysis_id: string
  dataset_id: string
  warehouse_id: string
  warehouse_name: string
  snapshot_at: string
  demand_source: string
  status: string
  assumptions: string[]
  statistics: { skus: number; locations: number; inventory_rows: number; demand_lines: number; candidate_locations: number }
  metrics: {
    assigned_skus: number; unassigned_skus: number
    baseline_pick_distance_m: number; proposed_pick_distance_m: number
    comparable_baseline_m: number; comparable_proposed_m: number
    estimated_distance_improvement_pct: number | null; optimization_score: number | null
    theoretical_travel_score_ceiling_pct: number | null
    unserved_pick_count: number
    baseline_utilization_pct: number | null; proposed_utilization_pct: number | null
    baseline_replenishment_cycles: number | null; estimated_replenishment_cycles: number | null
    constraint_violations: Record<string, number>
  }
  validation: { warnings: { code: string; detail: string }[]; incidents: { code: string; warehouse_id: string; location_id: string; detail: string }[]; blocked_locations: Record<string, string[]> }
  recommendations: {
    sku_id: string; sku_name: string; current_location_id: string | null; proposed_location_id: string | null
    decision: string; status: string; pick_count: number; demand_units: number
    demand_velocity_picks_per_day: number | null; current_round_trip_m: number | null
    proposed_round_trip_m: number | null; current_estimated_refill_cycles: number | null; estimated_refill_cycles: number | null
    reason: string; constraint_violations: string[]
  }[]
  movement_lines: { sku_id: string; source_location_id: string; proposed_location_id: string; packaging_profile_id: string; lot_id: string | null; handling_unit_count: number; on_hand_qty: number; base_uom: string; stock_status: string; action: string }[]
}

export type LiveEvaluation = { observed_skus: number; proposed_skus: number; coverage_pct: number | null; placement_agreement_pct: number; evaluated_moves: number; move_agreement_pct: number | null; measured_routes: number; route_mae_m: number | null; note: string }

const base = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

export function apiUrl(path: string): string {
  return `${base}/api${path}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(apiUrl(path), init)
  } catch {
    throw new Error('Cannot reach the API. Start the FastAPI server and try again.')
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json() as { detail?: string }
      if (body.detail) detail = body.detail
    } catch { /* Keep the HTTP status message. */ }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export const api = {
  overview: (warehouseId: string) => request<Overview>(`/overview${warehouseId ? `?warehouse_id=${encodeURIComponent(warehouseId)}` : ''}`),
  warehouses: () => request<Warehouse[]>('/warehouses'),
  recommendations: (params: { warehouseId: string; search: string; sort: string; offset: number; limit: number }) => {
    const query = new URLSearchParams({ search: params.search, sort: params.sort, offset: String(params.offset), limit: String(params.limit) })
    if (params.warehouseId) query.set('warehouse_id', params.warehouseId)
    return request<RecommendationPage>(`/recommendations?${query}`)
  },
  exceptions: () => request<{ items: ExceptionCase[] }>('/exceptions'),
  validation: () => request<Validation>('/validation'),
  forecast: () => request<Forecast>('/demand-forecast'),
  optimize: () => request<Overview['metrics']>('/optimize', { method: 'POST' }),
  optimizeUpload: (files: File[], warehouseName: string, snapshotAt: string) => {
    const form = new FormData()
    form.append('warehouse_name', warehouseName)
    form.append('snapshot_at', snapshotAt)
    files.forEach(file => form.append('files', file))
    return request<LiveResult>('/live/optimize', { method: 'POST', body: form })
  },
  evaluateLive: (analysisId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<LiveEvaluation>(`/live/evaluate/${analysisId}`, { method: 'POST', body: form })
  },
}
