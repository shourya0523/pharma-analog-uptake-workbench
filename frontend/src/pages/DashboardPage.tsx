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

const FILTER_KEYS = [
  'product_name',
  'therapeutic_area',
  'moa',
  'roa',
  'manufacturer',
  'validation_status',
] as const

type FilterKey = (typeof FILTER_KEYS)[number]

const FILTER_LABELS: Record<FilterKey, string> = {
  product_name: 'Product / analog',
  therapeutic_area: 'Therapeutic area',
  moa: 'MOA',
  roa: 'ROA',
  manufacturer: 'Manufacturer',
  validation_status: 'Validation status',
}

const CHART_COLORS = ['#1d4ed8', '#0f766e', '#b45309', '#7c3aed', '#be123c', '#0369a1', '#c2410c']

function uniqueOptions(products: any[], key: FilterKey, apiOptions?: Record<string, string[]>): string[] {
  const fromApi = apiOptions?.[key]
  if (fromApi?.length) return fromApi
  const seen = new Set<string>()
  const out: string[] = []
  for (const p of products) {
    const raw = String(p[key] ?? '').trim()
    if (!raw) continue
    const k = raw.toLowerCase()
    if (seen.has(k)) continue
    seen.add(k)
    out.push(raw)
  }
  return out.sort((a, b) => a.localeCompare(b))
}

export default function DashboardPage() {
  const { runId } = useParams()
  const q = useQuery({
    queryKey: ['dash', runId],
    queryFn: () => api.dashboard(runId),
    refetchInterval: 5000,
  })
  const [tab, setTab] = useState<'annual' | 'quarterly' | 'monthly' | 'methodology'>('quarterly')
  const [filters, setFilters] = useState<Partial<Record<FilterKey, string>>>({})
  const [drill, setDrill] = useState<any>(null)

  const allProducts = q.data?.products || []

  const products = useMemo(() => {
    return allProducts.filter((p: any) =>
      FILTER_KEYS.every((k) => {
        const selected = filters[k]
        if (!selected) return true
        return String(p[k] || '').toLowerCase() === selected.toLowerCase()
      }),
    )
  }, [allProducts, filters])

  const chartData = useMemo(() => {
    const selected = new Set(products.map((p: any) => p.product_name))
    let series = (q.data?.series || []).filter((s: any) => selected.has(s.product))
    if (tab === 'quarterly') {
      series = series.filter(
        (s: any) => (s.period_type || '').toLowerCase() === 'quarterly' || /Q[1-4]/i.test(String(s.period)),
      )
    } else if (tab === 'annual') {
      series = series.filter(
        (s: any) =>
          (s.period_type || '').toLowerCase() === 'annual' ||
          (/^\d{4}$/.test(String(s.period)) && !/Q/i.test(String(s.period))),
      )
    }
    const byPeriod: Record<string, any> = {}
    for (const s of series) {
      byPeriod[s.period] = byPeriod[s.period] || { period: s.period }
      byPeriod[s.period][s.product] = s.value
      byPeriod[s.period][`__meta_${s.product}`] = s
    }
    return Object.values(byPeriod).sort((a: any, b: any) => String(a.period).localeCompare(String(b.period)))
  }, [q.data, products, tab])

  const names = useMemo(
    () => Array.from(new Set(products.map((p: any) => p.product_name))),
    [products],
  )

  const activeFilterCount = FILTER_KEYS.filter((k) => filters[k]).length

  if (q.isLoading) return <div className="page">Loading dashboard…</div>

  return (
    <div className="dash-layout">
      <aside className="filter-panel">
        <div className="filter-panel-head">
          <h2>Analog Product Explorer</h2>
          <p className="filter-sub">
            {products.length} analog{products.length === 1 ? '' : 's'}
            {q.data?.analog_count != null && q.data.analog_count !== products.length
              ? ` of ${q.data.analog_count}`
              : ''}
            {activeFilterCount ? ` · ${activeFilterCount} filter${activeFilterCount === 1 ? '' : 's'}` : ''}
          </p>
        </div>

        <h3>Filters</h3>
        {FILTER_KEYS.map((f) => {
          const options = uniqueOptions(allProducts, f, q.data?.filter_options)
          return (
            <label key={f} className="filter-field">
              <span>{FILTER_LABELS[f]}</span>
              <select
                value={filters[f] || ''}
                onChange={(e) => {
                  const next = { ...filters }
                  if (e.target.value) next[f] = e.target.value
                  else delete next[f]
                  setFilters(next)
                }}
              >
                <option value="">All</option>
                {options.map((opt) => (
                  <option key={opt} value={opt}>
                    {opt}
                  </option>
                ))}
              </select>
            </label>
          )
        })}

        <button
          type="button"
          className="filter-clear"
          disabled={!activeFilterCount}
          onClick={() => setFilters({})}
        >
          Clear filters
        </button>
        <p className="muted small filter-hint">
          One row per analog (duplicates collapsed). Dropdowns use exact values from the run.
        </p>
      </aside>

      <div className="dash-main">
        <div className="dash-toolbar">
          <div className="dash-tabs">
            <button className={tab === 'annual' ? 'active' : ''} onClick={() => setTab('annual')}>
              Annual Uptake
            </button>
            <button className={tab === 'quarterly' ? 'active' : ''} onClick={() => setTab('quarterly')}>
              Quarterly Uptake
            </button>
            <button className={tab === 'monthly' ? 'active' : ''} onClick={() => setTab('monthly')}>
              Monthly Estimates
            </button>
            <button className={tab === 'methodology' ? 'active' : ''} onClick={() => setTab('methodology')}>
              Methodology
            </button>
          </div>
        </div>

        <div className="chart-wrap">
          {tab === 'monthly' ? (
            <p className="muted">Monthly estimates unavailable unless derivable from source-backed data.</p>
          ) : tab === 'methodology' ? (
            <div className="methodology">
              <p>Analogs shown: {products.length} (deduplicated by product name)</p>
              <p>Source-backed series points in view: {chartData.length} periods</p>
              <p>Click any chart point or Source cell for citation drill-through.</p>
            </div>
          ) : names.length === 0 ? (
            <p className="muted">No analogs match the current filters.</p>
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
                    stroke={CHART_COLORS[i % CHART_COLORS.length]}
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

        <div className="table-scroll">
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
