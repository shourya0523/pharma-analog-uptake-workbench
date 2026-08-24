import { useState } from 'react'
import { api } from '../api/client'

export default function ExportPage() {
  const runId = localStorage.getItem('lastRunId') || ''
  const [jobId, setJobId] = useState('')
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')

  async function doExport(format: string) {
    setError('')
    setMsg('')
    try {
      const body =
        format === 'product_workbook'
          ? { format, job_id: jobId }
          : { format: 'powerbi', run_id: runId }
      const res = await api.createExport(body)
      if (res.export_id) {
        window.open(api.downloadUrl(res.export_id), '_blank')
        setMsg(`Downloaded export ${res.export_id}`)
      } else if (res.exports) {
        for (const e of res.exports) {
          window.open(api.downloadUrl(e.id), '_blank')
        }
        setMsg(`Started ${res.exports.length} Power BI downloads`)
      }
    } catch (e: any) {
      setError(e.message)
    }
  }

  return (
    <div className="page">
      <h1>Export</h1>
      <p className="muted">Unresolved and needs-review states remain explicit. Confirmed rows require citations.</p>
      <section className="card-block">
        <h2>Selected product workbook</h2>
        <input placeholder="Job ID" value={jobId} onChange={(e) => setJobId(e.target.value)} />
        <button disabled={!jobId} onClick={() => doExport('product_workbook')}>
          Export product Excel
        </button>
      </section>
      <section className="card-block">
        <h2>Power BI CSVs (run)</h2>
        <p>Run: {runId || 'none'}</p>
        <button disabled={!runId} onClick={() => doExport('powerbi')}>
          Export Power BI files
        </button>
      </section>
      {msg && <p>{msg}</p>}
      {error && <p className="error">{error}</p>}
    </div>
  )
}
