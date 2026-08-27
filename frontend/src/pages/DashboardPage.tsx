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
import {
  buildChartData,
  calculateFilteredKpis,
  filterProducts,
  FILTER_KEYS,
  type FilterKey,
  uniqueOptions,
} from './dashboardModel'

const FILTER_LABELS: Record<FilterKey, string> = {
  product_name: 'Product / analog',
  therapeutic_area: 'Therapeutic area',
  company: 'Company',
  approval_period: 'Initial FDA approval',
  competitive_intensity: 'Competitive intensity',
  moa: 'MOA',
  roa: 'ROA',
  peak_sales_bucket: 'Peak-sales potential',
  indication_count: 'Approved indication count',
}

const CHART_COLORS = ['#1d4ed8', '#0f766e', '#b45309', '#7c3aed', '#be123c', '#0369a1', '#c2410c']

export default function DashboardPage() {
  const { runId } = useParams()
  const q = useQuery({
    queryKey: ['dash', runId],
    queryFn: () => api.dashboard(runId),
    refetchInterval: 5000,
  })
  const [tab, setTab] = useState<'annual' | 'quarterly' | 'launch' | 'launch24' | 'methodology'>('quarterly')
  const [filters, setFilters] = useState<Partial<Record<FilterKey, string>>>({})
  const [drill, setDrill] = useState<any>(null)

  const allProducts = useMemo(() => q.data?.products || [], [q.data?.products])

  const products = useMemo(() => filterProducts(allProducts, filters), [allProducts, filters])
  const chartData = useMemo(() => buildChartData(q.data, products, tab), [q.data, products, tab])
  const kpis = useMemo(() => calculateFilteredKpis(products), [products])

  const names = useMemo(
    () => Array.from(new Set(products.map((p: any) => String(p.product_name)))) as string[],
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
          const options = uniqueOptions(allProducts, f, filters, q.data?.filter_options)
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
            <button type="button" className={tab === 'annual' ? 'active' : ''} onClick={() => setTab('annual')}>
              Annual Uptake
            </button>
            <button type="button" className={tab === 'quarterly' ? 'active' : ''} onClick={() => setTab('quarterly')}>
              Quarterly Uptake
            </button>
            <button type="button" className={tab === 'launch' ? 'active' : ''} onClick={() => setTab('launch')}>
              Launch-relative
            </button>
            <button type="button" className={tab === 'launch24' ? 'active' : ''} onClick={() => setTab('launch24')}>
              First 24 months
            </button>
            <button type="button" className={tab === 'methodology' ? 'active' : ''} onClick={() => setTab('methodology')}>
              Methodology
            </button>
          </div>
        </div>

        <section className="kpi-grid" aria-label="Filtered cohort KPIs">
          <article><span>Products tracked</span><strong>{kpis.productsTracked}</strong></article>
          <article><span>Companies represented</span><strong>{kpis.companiesRepresented}</strong></article>
          <article>
            <span>Aggregate selected peak</span>
            <strong>${kpis.aggregatePeak.toLocaleString()}M</strong>
            <small>Coverage {kpis.peakCoverage}</small>
          </article>
          <article><span>Uptake-ready products</span><strong>{kpis.uptakeReady}</strong></article>
        </section>

        <div className="chart-wrap">
          {tab === 'methodology' ? (
            <div className="methodology">
              <p>Canonical products shown: {products.length} (formulations remain commercially distinct)</p>
              <p>Source-backed series points in view: {chartData.length} periods</p>
              <p>Launch uptake is a rolling-four-quarter revenue proxy divided by the typed selected annual peak.</p>
              <p>Competitive intensity uses the stored competitive_intensity_v1 peer cohort and score components.</p>
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
                <th>Competitive intensity</th>
                <th>Reached peak yet</th>
                <th>Estimated peak revenue</th>
                <th>Peak type</th>
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
                  <td>{p.therapeutic_area || 'Unresolved'}</td>
                  <td>{p.company || p.manufacturer || 'Unresolved'}</td>
                  <td>{p.fda_approval_date || 'Unresolved'}</td>
                  <td>{p.approved_indications || 'Unresolved'}</td>
                  <td>{p.moa || 'Unresolved'}</td>
                  <td>{p.roa || 'Unresolved'}</td>
                  <td>{p.treatment_type || 'Not applicable'}</td>
                  <td>{p.approved_lot || 'Unresolved'}</td>
                  <td>{p.competitive_intensity || 'Unresolved'}</td>
                  <td>{p.reached_peak_yet || 'Unresolved'}</td>
                  <td>{p.estimated_peak_revenue ?? 'Unresolved'}</td>
                  <td>{p.peak_type || 'Unresolved'}</td>
                  <td>{p.time_to_peak || 'Unresolved'}</td>
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
