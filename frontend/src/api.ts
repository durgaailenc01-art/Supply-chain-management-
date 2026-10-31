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
}
