const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, init)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || res.statusText)
  }
  return res.json()
}

export const api = {
  createRun: (body: unknown) =>
    req<{ run_id: string; job_count: number }>('/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  getRun: (runId: string) => req<any>(`/runs/${runId}`),
  getJob: (jobId: string) => req<any>(`/jobs/${jobId}`),
  patchDatapoint: (id: string, body: unknown) =>
    req(`/datapoints/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  validationAction: (id: string, body: unknown) =>
    req(`/validation-tasks/${id}/actions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
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
  createExport: (body: unknown) =>
    req<any>('/exports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  downloadUrl: (exportId: string) => `${API}/exports/${exportId}/download`,
}

export { API }
