import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { API, api } from '../api/client'

/**
 * Adding a product and deciding how often it re-runs is one decision, so it is
 * one form. The window defaults to the full lifecycle because that is what the
 * backfill is for; the cadence defaults to quarterly because keeping products
 * current is the point of the tool.
 */
export default function AddProductsPage() {
  const nav = useNavigate()
  const [paste, setPaste] = useState('')
  const [windowMode, setWindowMode] = useState<'full' | 'custom'>('full')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [cadence, setCadence] = useState<'quarterly' | 'one_off'>('quarterly')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const drugs = paste
    .split(/\n|,/)
    .map((s) => s.trim())
    .filter(Boolean)

  async function start() {
    setBusy(true)
    setError('')
    try {
      const res = await api.createRun({
        drugs: drugs.map((drug_name) => ({ drug_name })),
        options:
          windowMode === 'custom'
            ? {
                earnings_since: since || null,
                earnings_until: until || null,
              }
            : {},
      })
      localStorage.setItem('lastRunId', res.run_id)
      // Cadence is a property of the product, which only exists once identity
      // has resolved it, so it is applied from the Library after the run lands.
      localStorage.setItem('pendingCadence', JSON.stringify({ cadence, drugs }))
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

  return (
    <div className="page">
      <div className="crumb">
        <button className="linkish" onClick={() => nav('/')}>
          Library
        </button>{' '}
        / Add products
      </div>
      <h1>Add products to coverage</h1>

      <div className="add-layout">
        <div>
          <section className="card-block">
            <div className="step-head">
              <span className="step-num">1</span>
              <h2>Which products</h2>
              <span className="muted small">{drugs.length} parsed</span>
            </div>
            <textarea
              rows={5}
              value={paste}
              placeholder={'One product per line\nOpsumit\nTyvaso DPI'}
              onChange={(e) => setPaste(e.target.value)}
            />
            <div className="or-row">
              <input
                type="file"
                accept=".csv"
                aria-label="Upload CSV"
                onChange={(e) => e.target.files?.[0] && startCsv(e.target.files[0])}
              />
              <span className="muted small">or upload the seed CSV</span>
            </div>
          </section>

          <section className="card-block">
            <div className="step-head">
              <span className="step-num">2</span>
              <h2>How far back</h2>
            </div>
            <label className={`radio-card ${windowMode === 'full' ? 'active' : ''}`}>
              <input
                type="radio"
                name="window"
                checked={windowMode === 'full'}
                onChange={() => setWindowMode('full')}
              />
              <span>
                <span className="rc-title">Full product lifecycle</span>
                <span className="rc-sub">
                  Every quarter the filings cover, from first commercial sale through the
                  current quarter.
                </span>
              </span>
            </label>
            <label className={`radio-card ${windowMode === 'custom' ? 'active' : ''}`}>
              <input
                type="radio"
                name="window"
                checked={windowMode === 'custom'}
                onChange={() => setWindowMode('custom')}
              />
              <span>
                <span className="rc-title">Custom range</span>
                <span className="rc-sub">
                  Bound the earnings filings the run retrieves to a window.
                </span>
              </span>
            </label>
            {windowMode === 'custom' && (
              <div className="range-row">
                <label>
                  From
                  <input type="date" value={since} onChange={(e) => setSince(e.target.value)} />
                </label>
                <label>
                  To
                  <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
                </label>
              </div>
            )}
          </section>

          <section className="card-block">
            <div className="step-head">
              <span className="step-num">3</span>
              <h2>Cadence</h2>
            </div>
            <label className={`radio-card ${cadence === 'quarterly' ? 'active' : ''}`}>
              <input
                type="radio"
                name="cadence"
                checked={cadence === 'quarterly'}
                onChange={() => setCadence('quarterly')}
              />
              <span>
                <span className="rc-title">Scheduled — every quarter</span>
                <span className="rc-sub">
                  Re-runs as each issuer files, picking up the newest quarter without
                  anyone starting a run.
                </span>
              </span>
            </label>
            <label className={`radio-card ${cadence === 'one_off' ? 'active' : ''}`}>
              <input
                type="radio"
                name="cadence"
                checked={cadence === 'one_off'}
                onChange={() => setCadence('one_off')}
              />
              <span>
                <span className="rc-title">One-off</span>
                <span className="rc-sub">
                  Pull the backfill once. Nothing re-runs until someone asks.
                </span>
              </span>
            </label>
          </section>
        </div>

        <aside className="card-block">
          <h2>Summary</h2>
          <div className="side-stat">
            <span>Products</span>
            <strong>{drugs.length}</strong>
          </div>
          <div className="side-stat">
            <span>Window</span>
            <strong>{windowMode === 'full' ? 'Full lifecycle' : 'Custom'}</strong>
          </div>
          <div className="side-stat">
            <span>Cadence</span>
            <strong>{cadence === 'quarterly' ? 'Every quarter' : 'One-off'}</strong>
          </div>
          <p className="side-note">
            {cadence === 'quarterly'
              ? 'After the backfill these products re-run on their own. New quarters land in the review queue if the judge flags them.'
              : 'These products will be pulled once. You can switch them to a quarterly cadence later from the Library.'}
          </p>
          <button disabled={busy || !drugs.length} onClick={start}>
            {drugs.length
              ? `Start backfill · ${drugs.length} product${drugs.length === 1 ? '' : 's'}`
              : 'Start backfill'}
          </button>
          <button className="ghost" onClick={() => nav('/')}>
            Cancel
          </button>
          {error && <p className="error">{error}</p>}
        </aside>
      </div>
    </div>
  )
}
