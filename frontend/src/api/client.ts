const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, init)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(detailOf(text) || res.statusText)
  }
  return res.json()
}

/** FastAPI puts the useful message in `detail`; surface that rather than raw JSON. */
function detailOf(text: string): string {
  try {
    const parsed = JSON.parse(text)
    if (typeof parsed?.detail === 'string') return parsed.detail
    if (Array.isArray(parsed?.detail)) {
      return parsed.detail.map((d: any) => d?.msg).filter(Boolean).join('; ')
    }
  } catch {
    // not JSON; the body is already the message
  }
  return text
}

function json(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export type ProductRow = {
  id: string
  name: string
  generic: string | null
  company: string | null
  indication: string | null
  moa: string | null
  roa: string | null
  cadence: string
  completeness_pct: number
  quarters: number
  flagged: number
  missing: number
  last_job_id: string | null
  last_run_at: string | null
  last_run_status: string | null
}

export type ReviewItem = {
  id: string
  type: 'flagged' | 'missing'
  product_id: string | null
  product: string
  job_id: string
  period: string
  reason: string
  confidence: number
  datapoint_id?: string
  value_normalized_usd_millions?: number | null
  revenue_scope?: string
  source_url?: string
  source_quote?: string
  extraction_method?: string
  validation_status?: string
  reason_unresolved?: string
  sources_checked?: string[]
  recommended_next_step?: string
  reviewer_notes?: string | null
}

export const api = {
  createRun: (body: unknown) =>
    req<{ run_id: string; job_count: number }>('/runs', json('POST', body)),
  getRun: (runId: string) => req<any>(`/runs/${runId}`),
  getJob: (jobId: string) => req<any>(`/jobs/${jobId}`),
  patchDatapoint: (id: string, body: unknown) => req(`/datapoints/${id}`, json('PATCH', body)),
  validationAction: (id: string, body: unknown) =>
    req(`/validation-tasks/${id}/actions`, json('POST', body)),

  listProducts: (params?: { cadence?: string; q?: string }) => {
    const qs = new URLSearchParams()
    if (params?.cadence) qs.set('cadence', params.cadence)
    if (params?.q) qs.set('q', params.q)
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<{ products: ProductRow[]; total: number }>(`/products${suffix}`)
  },
  getProduct: (productId: string) => req<any>(`/products/${productId}`),
  setCadence: (productId: string, cadence: string) =>
    req<any>(`/products/${productId}`, json('PATCH', { cadence })),
  setCadenceBulk: (productIds: string[], cadence: string) =>
    req<{ updated: number }>('/products/cadence', json('POST', { product_ids: productIds, cadence })),
  reviewQueue: (params?: { product_id?: string; item_type?: string; reason?: string }) => {
    const qs = new URLSearchParams()
    if (params?.product_id) qs.set('product_id', params.product_id)
    if (params?.item_type) qs.set('item_type', params.item_type)
    if (params?.reason) qs.set('reason', params.reason)
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<{ items: ReviewItem[]; total: number; flagged: number; missing: number }>(
      `/review/queue${suffix}`,
    )
  },
  resolveUnresolved: (id: string, body: unknown) =>
    req<any>(`/unresolved-quarters/${id}/actions`, json('POST', body)),
  patchProfileField: (id: string, body: unknown) =>
    req<any>(`/profile-fields/${id}`, json('PATCH', body)),
  dashboard: (runId?: string) =>
    req<any>(`/dashboard/preview${runId ? `?run_id=${runId}` : ''}`),
  observability: () => req<any>('/observability'),
  observabilityLogs: (params?: { limit?: number; level?: string; q?: string; logger?: string }) => {
    const qs = new URLSearchParams()
    if (params?.limit != null) qs.set('limit', String(params.limit))
    if (params?.level) qs.set('level', params.level)
    if (params?.q) qs.set('q', params.q)
    if (params?.logger) qs.set('logger_name', params.logger)
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<any>(`/observability/logs${suffix}`)
  },
  observabilityTable: (
    table: string,
    params?: { limit?: number; offset?: number; run_id?: string; job_id?: string; q?: string },
  ) => {
    const qs = new URLSearchParams()
    if (params?.limit != null) qs.set('limit', String(params.limit))
    if (params?.offset != null) qs.set('offset', String(params.offset))
    if (params?.run_id) qs.set('run_id', params.run_id)
    if (params?.job_id) qs.set('job_id', params.job_id)
    if (params?.q) qs.set('q', params.q)
    const suffix = qs.toString() ? `?${qs}` : ''
    return req<any>(`/observability/db/${encodeURIComponent(table)}${suffix}`)
  },
  createExport: (body: unknown) => req<any>('/exports', json('POST', body)),
  downloadUrl: (exportId: string) => `${API}/exports/${exportId}/download`,
}

export { API }
