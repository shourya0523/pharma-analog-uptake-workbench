import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { API, api } from '../api/client'

export default function SetupPage() {
  const nav = useNavigate()
  const [paste, setPaste] = useState('Opsumit\nAdcirca\nTyvaso')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [templateInfo, setTemplateInfo] = useState<any>(null)

  async function startPaste() {
    setBusy(true)
    setError('')
    try {
      const drugs = paste
        .split(/\n|,/)
        .map((s) => s.trim())
        .filter(Boolean)
        .map((drug_name) => ({ drug_name }))
      const res = await api.createRun({ drugs, options: {} })
      localStorage.setItem('lastRunId', res.run_id)
      nav(`/monitor/${res.run_id}`)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function startCsv(file: File) {
    setBusy(true)
    setError('')
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('options_json', '{}')
      const res = await fetch(`${API}/runs/from-csv`, { method: 'POST', body: fd })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      localStorage.setItem('lastRunId', data.run_id)
      nav(`/monitor/${data.run_id}`)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function inferTemplate(file: File) {
    setError('')
    try {
      const fd = new FormData()
      fd.append('file', file)
      const res = await fetch(`${API}/runs/infer-template`, { method: 'POST', body: fd })
      if (!res.ok) throw new Error(await res.text())
      setTemplateInfo(await res.json())
    } catch (e: any) {
      setError(e.message)
    }
  }

  return (
    <div className="page">
      <h1>Run setup</h1>
      <p className="muted">Paste or upload a drug list. Citations are required for every source-derived field.</p>
      <section className="card-block">
        <h2>Paste drug list</h2>
        <textarea value={paste} onChange={(e) => setPaste(e.target.value)} rows={8} />
        <button disabled={busy} onClick={startPaste}>
          Start Extraction Run
        </button>
      </section>
      <section className="card-block">
        <h2>Upload CSV</h2>
        <input
          type="file"
          accept=".csv"
          onChange={(e) => e.target.files?.[0] && startCsv(e.target.files[0])}
        />
      </section>
      <section className="card-block">
        <h2>Dashboard workbook template (optional)</h2>
        <input
          type="file"
          accept=".xlsx,.xls"
          onChange={(e) => e.target.files?.[0] && inferTemplate(e.target.files[0])}
        />
        {templateInfo && (
          <pre className="code">{JSON.stringify(templateInfo, null, 2)}</pre>
        )}
      </section>
      {error && <p className="error">{error}</p>}
    </div>
  )
}
