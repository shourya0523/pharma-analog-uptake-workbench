import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '../api/client'

const FILTERS = [
  'therapeutic_area',
  'moa',
  'roa',
  'manufacturer',
  'validation_status',
] as const

export default function DashboardPage() {
  const { runId } = useParams()
  const q = useQuery({
    queryKey: ['dash', runId],
    queryFn: () => api.dashboard(runId),
    refetchInterval: 5000,
  })
  const [tab, setTab] = useState<'annual' | 'quarterly' | 'monthly' | 'methodology'>('quarterly')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [drill, setDrill] = useState<any>(null)

  const products = useMemo(() => {
    const list = q.data?.products || []
    return list.filter((p: any) =>
      FILTERS.every((k) => !filters[k] || String(p[k] || '').toLowerCase().includes(filters[k].toLowerCase())),
    )
  }, [q.data, filters])

  const chartData = useMemo(() => {
    const selected = new Set(products.map((p: any) => p.product_name))
    const series = (q.data?.series || []).filter((s: any) => selected.has(s.product))
    const byPeriod: Record<string, any> = {}
    for (const s of series) {
      byPeriod[s.period] = byPeriod[s.period] || { period: s.period }
      byPeriod[s.period][s.product] = s.value
      byPeriod[s.period][`__meta_${s.product}`] = s
    }
    return Object.values(byPeriod).sort((a: any, b: any) => String(a.period).localeCompare(String(b.period)))
  }, [q.data, products])

  const names = products.map((p: any) => p.product_name)

  if (q.isLoading) return <div className="page">Loading dashboard…</div>

  return (
    <div className="dash-layout">
      <aside className="filter-panel">
        <h2>Analog Product Explorer</h2>
        <h3>Filter Panel</h3>
        {FILTERS.map((f) => (
          <label key={f}>
            {f.replace(/_/g, ' ')}
            <input
              value={filters[f] || ''}
              onChange={(e) => setFilters({ ...filters, [f]: e.target.value })}
            />
          </label>
        ))}
        <p className="muted small">Also: FDA year, LoT, competitive intensity when present in profile.</p>
      </aside>

      <div className="dash-main">
        <div className="chart-wrap">
          {tab === 'monthly' ? (
            <p className="muted">Monthly estimates unavailable unless derivable from source-backed data.</p>
          ) : tab === 'methodology' ? (
            <div className="methodology">
              <p>Products: {products.length}</p>
              <p>Source-backed series points: {(q.data?.series || []).length}</p>
              <p>Click any chart point or Link cell for citation drill-through.</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="period" />
                <YAxis />
                <Tooltip />
                <Legend />
                {names.map((n: string, i: number) => (
                  <Line
                    key={n}
                    type="monotone"
                    dataKey={n}
                    stroke={['#1d4ed8', '#0f766e', '#b45309', '#7c3aed', '#be123c'][i % 5]}
                    dot={{
                      onClick: (_: any, payload: any) => {
                        const meta = payload?.payload?.[`__meta_${n}`]
                        if (meta) setDrill(meta)
                      },
                    }}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>

        <table className="grid">
          <thead>
            <tr>
              <th>Product name</th>
              <th>Therapeutic area</th>
              <th>Manufacturer</th>
              <th>FDA approval date</th>
              <th>Approved indications</th>
              <th>MOA</th>
              <th>ROA</th>
              <th>Treatment type</th>
              <th>Approved LoT</th>
              <th>Reached peak yet</th>
              <th>Estimated peak revenue</th>
              <th>Time-to-peak</th>
              <th>Link</th>
              <th>Completeness</th>
              <th>Validation</th>
            </tr>
          </thead>
          <tbody>
            {products.map((p: any) => (
              <tr key={p.job_id}>
                <td>{p.product_name}</td>
                <td>{p.therapeutic_area}</td>
                <td>{p.manufacturer}</td>
                <td>{p.fda_approval_date}</td>
                <td>{p.approved_indications}</td>
                <td>{p.moa}</td>
                <td>{p.roa}</td>
                <td>{p.treatment_type}</td>
                <td>{p.approved_lot}</td>
                <td>{p.reached_peak_yet}</td>
                <td>{p.estimated_peak_revenue}</td>
                <td>{p.time_to_peak}</td>
                <td>
                  {p.source_link ? (
                    <button
                      className="linkish"
                      onClick={() =>
                        setDrill({
                          source_url: p.source_link,
                          source_quote: 'See product datapoints for quotes',
                          product: p.product_name,
                        })
                      }
                    >
                      Source
                    </button>
                  ) : (
                    '—'
                  )}
                </td>
                <td>{p.completeness_score}%</td>
                <td>{p.validation_status}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="dash-tabs">
          <button className={tab === 'annual' ? 'active' : ''} onClick={() => setTab('annual')}>
            Annual Uptake
          </button>
          <button className={tab === 'quarterly' ? 'active' : ''} onClick={() => setTab('quarterly')}>
            Quarterly Uptake
          </button>
          <button className={tab === 'monthly' ? 'active' : ''} onClick={() => setTab('monthly')}>
            Monthly Uptake Estimates
          </button>
          <button className={tab === 'methodology' ? 'active' : ''} onClick={() => setTab('methodology')}>
            Methodology
          </button>
        </div>
      </div>

      {drill && (
        <div className="drawer">
          <button className="close" onClick={() => setDrill(null)}>
            ×
          </button>
          <h3>Source drill-through</h3>
          <p>
            <strong>URL:</strong>{' '}
            <a href={drill.source_url} target="_blank" rel="noreferrer">
              {drill.source_url}
            </a>
          </p>
          <p>
            <strong>Quote:</strong> {drill.source_quote}
          </p>
          <p>
            <strong>Validation:</strong> {drill.validation_status}
          </p>
          <p>
            <strong>Flags:</strong> {(drill.issue_flags || []).join(', ') || '—'}
          </p>
          <p>
            <strong>Notes:</strong> {drill.reviewer_notes || '—'}
          </p>
          <pre className="code">{JSON.stringify(drill.citation || {}, null, 2)}</pre>
        </div>
      )}
    </div>
  )
}
