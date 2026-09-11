import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { api, type ProductRow } from '../api/client'

const CADENCE_LABEL: Record<string, string> = {
  quarterly: 'Every quarter',
  one_off: 'One-off',
}

export default function LibraryPage() {
  const nav = useNavigate()
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [company, setCompany] = useState('')
  const [moa, setMoa] = useState('')
  const [cadence, setCadence] = useState('')
  const [attention, setAttention] = useState('')
  const [selected, setSelected] = useState<Record<string, boolean>>({})

  const q = useQuery({
    queryKey: ['products'],
    queryFn: () => api.listProducts(),
  })

  const cadenceMutation = useMutation({
    mutationFn: ({ ids, value }: { ids: string[]; value: string }) =>
      api.setCadenceBulk(ids, value),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['products'] })
      setSelected({})
    },
  })

  // Memoised so the filter below is not re-run against a fresh array each render.
  const products: ProductRow[] = useMemo(() => q.data?.products ?? [], [q.data])

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return products.filter((p) => {
      if (
        needle &&
        !`${p.name} ${p.generic ?? ''}`.toLowerCase().includes(needle)
      )
        return false
      if (company && p.company !== company) return false
      if (moa && p.moa !== moa) return false
      if (cadence && p.cadence !== cadence) return false
      if (attention === 'flagged' && p.flagged <= 0) return false
      if (attention === 'missing' && p.missing <= 0) return false
      return true
    })
  }, [products, search, company, moa, cadence, attention])

  const options = (key: 'company' | 'moa') =>
    Array.from(new Set(products.map((p) => p[key]).filter(Boolean) as string[])).sort()

  const selectedIds = Object.keys(selected).filter((id) => selected[id])
  const kpis = {
    tracked: rows.length,
    scheduled: rows.filter((p) => p.cadence === 'quarterly').length,
    completeness: rows.length
      ? Math.round(rows.reduce((a, p) => a + p.completeness_pct, 0) / rows.length)
      : 0,
    flagged: rows.reduce((a, p) => a + p.flagged, 0),
    missing: rows.reduce((a, p) => a + p.missing, 0),
  }

  if (q.isLoading) return <div className="page">Loading library…</div>
  if (q.error) return <div className="page error">{(q.error as Error).message}</div>

  return (
    <div className="dash-layout">
      <aside className="filter-panel">
        <div className="filter-panel-head">
          <h2>Product Library</h2>
          <p className="filter-sub">
            Every analog under coverage. Cadence decides whether the pipeline re-runs it
            each quarter.
          </p>
        </div>
        <label className="filter-field">
          <span>Search</span>
          <input
            value={search}
            placeholder="Product or generic name"
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <label className="filter-field">
          <span>Company</span>
          <select value={company} onChange={(e) => setCompany(e.target.value)}>
            <option value="">All companies</option>
            {options('company').map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="filter-field">
          <span>Mechanism of action</span>
          <select value={moa} onChange={(e) => setMoa(e.target.value)}>
            <option value="">All MOAs</option>
            {options('moa').map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="filter-field">
          <span>Cadence</span>
          <select value={cadence} onChange={(e) => setCadence(e.target.value)}>
            <option value="">Any cadence</option>
            <option value="quarterly">Every quarter</option>
            <option value="one_off">One-off</option>
          </select>
        </label>
        <label className="filter-field">
          <span>Needs attention</span>
          <select value={attention} onChange={(e) => setAttention(e.target.value)}>
            <option value="">All statuses</option>
            <option value="flagged">Has flagged quarters</option>
            <option value="missing">Has missing quarters</option>
          </select>
        </label>
        <div className="filter-count">
          {rows.length} of {products.length} products shown
        </div>
        <button
          className="filter-clear"
          onClick={() => {
            setSearch('')
            setCompany('')
            setMoa('')
            setCadence('')
            setAttention('')
          }}
        >
          Clear filters
        </button>
      </aside>

      <section className="dash-main">
        <div className="main-head">
          <h1>Coverage</h1>
          <button onClick={() => nav('/add')}>+ Add products</button>
        </div>

        <div className="kpi-grid">
          <article>
            <span>Products tracked</span>
            <strong>{kpis.tracked}</strong>
            <small>{kpis.scheduled} on quarterly cadence</small>
          </article>
          <article>
            <span>Avg. completeness</span>
            <strong>{kpis.completeness}%</strong>
            <small>of filtered set</small>
          </article>
          <article>
            <span>Flagged for review</span>
            <strong>{kpis.flagged}</strong>
            <small>values the judge flagged</small>
          </article>
          <article>
            <span>Missing quarters</span>
            <strong>{kpis.missing}</strong>
            <small>expected but not found</small>
          </article>
        </div>

        <div className="card-block" style={{ padding: 0 }}>
          {selectedIds.length > 0 && (
            <div className="bulk-bar">
              <strong>{selectedIds.length} selected</strong>
              <span className="muted small">Set cadence:</span>
              <button
                className="ghost"
                disabled={cadenceMutation.isPending}
                onClick={() =>
                  cadenceMutation.mutate({ ids: selectedIds, value: 'quarterly' })
                }
              >
                Every quarter
              </button>
              <button
                className="ghost"
                disabled={cadenceMutation.isPending}
                onClick={() => cadenceMutation.mutate({ ids: selectedIds, value: 'one_off' })}
              >
                One-off
              </button>
              <button className="ghost" onClick={() => setSelected({})}>
                Clear selection
              </button>
            </div>
          )}

          <table className="grid">
            <thead>
              <tr>
                <th style={{ width: 28 }}></th>
                <th>Product</th>
                <th>Company</th>
                <th>Indication</th>
                <th>MOA / ROA</th>
                <th>Cadence</th>
                <th>Coverage</th>
                <th>Queue</th>
                <th>Last run</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Select ${p.name}`}
                      checked={!!selected[p.id]}
                      onChange={() =>
                        setSelected({ ...selected, [p.id]: !selected[p.id] })
                      }
                    />
                  </td>
                  <td>
                    <Link to={`/products/${p.id}`} className="name-link">
                      {p.name}
                    </Link>
                    <div className="muted small">{p.generic}</div>
                  </td>
                  <td>{p.company || '—'}</td>
                  <td>{p.indication || '—'}</td>
                  <td>
                    {p.moa || '—'}
                    <div className="muted small">{p.roa}</div>
                  </td>
                  <td>
                    <span className={`pill ${p.cadence === 'quarterly' ? 'sched' : 'oneoff'}`}>
                      {CADENCE_LABEL[p.cadence] || p.cadence}
                    </span>
                  </td>
                  <td>
                    <div className="completeness-bar">
                      <i style={{ width: `${Math.min(100, p.completeness_pct)}%` }} />
                    </div>
                    <div className="muted small">
                      {p.completeness_pct}% · {p.quarters} qtrs
                    </div>
                  </td>
                  <td>
                    <div className="queue-cell">
                      {p.flagged > 0 && (
                        <Link className="pill flagged" to={`/review?product=${p.id}`}>
                          {p.flagged} flagged
                        </Link>
                      )}
                      {p.missing > 0 && (
                        <Link className="pill missing" to={`/review?product=${p.id}`}>
                          {p.missing} missing
                        </Link>
                      )}
                      {p.flagged === 0 && p.missing === 0 && <span className="pill ok">clear</span>}
                    </div>
                  </td>
                  <td>
                    {p.last_run_at ? new Date(p.last_run_at).toLocaleDateString() : '—'}
                    <div className="muted small">{p.last_run_status || 'never run'}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {!rows.length && (
            <p className="muted" style={{ padding: '1.5rem', textAlign: 'center' }}>
              {products.length
                ? 'No products match these filters.'
                : 'No products under coverage yet. Add some to get started.'}
            </p>
          )}
        </div>

        {cadenceMutation.error && (
          <p className="error">{(cadenceMutation.error as Error).message}</p>
        )}
      </section>
    </div>
  )
}
