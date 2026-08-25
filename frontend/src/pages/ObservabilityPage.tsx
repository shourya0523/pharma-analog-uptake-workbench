import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

type Pane = 'logs' | 'database' | 'errors' | 'runs'

const LEVELS = ['', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const

export default function ObservabilityPage() {
  const [pane, setPane] = useState<Pane>('logs')
  const [logLevel, setLogLevel] = useState('')
  const [logQuery, setLogQuery] = useState('')
  const [logLogger, setLogLogger] = useState('')
  const [table, setTable] = useState('drug_jobs')
  const [tableQuery, setTableQuery] = useState('')
  const [runId, setRunId] = useState('')
  const [jobId, setJobId] = useState('')
  const [selected, setSelected] = useState<any>(null)

  const overview = useQuery({
    queryKey: ['obs-overview'],
    queryFn: () => api.observability(),
    refetchInterval: 4000,
  })

  const logs = useQuery({
    queryKey: ['obs-logs', logLevel, logQuery, logLogger],
    queryFn: () =>
      api.observabilityLogs({
        limit: 250,
        level: logLevel || undefined,
        q: logQuery || undefined,
        logger: logLogger || undefined,
      }),
    refetchInterval: pane === 'logs' ? 2000 : 8000,
  })

  const db = useQuery({
    queryKey: ['obs-db', table, tableQuery, runId, jobId],
    queryFn: () =>
      api.observabilityTable(table, {
        limit: 100,
        q: tableQuery || undefined,
        run_id: runId || undefined,
        job_id: jobId || undefined,
      }),
    refetchInterval: pane === 'database' ? 5000 : false,
    enabled: pane === 'database' || pane === 'runs',
  })

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setSelected(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const tables = overview.data?.available_tables || overview.data?.tables?.map((t: any) => t.name) || []
  const tableCounts = useMemo(() => {
    const map: Record<string, number> = {}
    for (const t of overview.data?.tables || []) map[t.name] = t.count
    return map
  }, [overview.data])

  const loggerOptions = useMemo(() => {
    const set = new Set<string>()
    for (const entry of logs.data?.logs || []) {
      if (entry.logger) set.add(entry.logger)
    }
    return Array.from(set).sort()
  }, [logs.data])

  function openDatabase(name: string) {
    setTable(name)
    setPane('database')
    setSelected(null)
  }

  if (overview.isLoading) return <div className="page">Loading observability…</div>

  return (
    <div className={`obs-layout ${selected ? 'has-drawer' : ''}`}>
      <aside className="obs-side">
        <h1>Observability</h1>
        <p className="muted small">
          Interact with live logs and the workbench database. Click any row or tile to inspect.
        </p>
        <div className="obs-health">
          <span className={`pill ${overview.data?.health?.status === 'ok' ? 'ok' : 'bad'}`}>
            {overview.data?.health?.status || 'unknown'}
          </span>
          <span className="muted small">{overview.data?.health?.environment}</span>
          <span className="muted small">log buffer {overview.data?.log_buffer_size ?? 0}</span>
        </div>

        <nav className="obs-nav" aria-label="Observability panes">
          {(
            [
              ['logs', 'Live logs'],
              ['database', 'Database'],
              ['errors', 'Job errors'],
              ['runs', 'Recent runs'],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={pane === id ? 'active' : ''}
              onClick={() => {
                setPane(id)
                setSelected(null)
              }}
            >
              {label}
            </button>
          ))}
        </nav>

        <h3 className="obs-section-label">Tables</h3>
        <div className="obs-stat-grid">
          {(overview.data?.tables || []).map((t: any) => (
            <button
              key={t.name}
              type="button"
              className={`obs-stat ${pane === 'database' && table === t.name ? 'active' : ''}`}
              onClick={() => openDatabase(t.name)}
            >
              <strong>{t.count}</strong>
              <span>{t.name}</span>
            </button>
          ))}
        </div>

        <div className="obs-status-block">
          <h3>Job statuses</h3>
          <ul>
            {Object.entries(overview.data?.job_status_counts || {}).map(([status, count]) => (
              <li key={status}>
                <code>{status}</code> <span>{count as number}</span>
              </li>
            ))}
            {!Object.keys(overview.data?.job_status_counts || {}).length && (
              <li className="muted">No jobs yet</li>
            )}
          </ul>
        </div>
      </aside>

      <section className="obs-main">
        {pane === 'logs' && (
          <>
            <header className="obs-pane-head">
              <h2>Live logs</h2>
              <p className="muted small">Ring buffer from the API process. Click a line to inspect.</p>
            </header>
            <div className="obs-level-chips" role="group" aria-label="Log level filter">
              {LEVELS.map((l) => (
                <button
                  key={l || 'all'}
                  type="button"
                  className={logLevel === l ? 'active' : ''}
                  onClick={() => setLogLevel(l)}
                >
                  {l || 'All'}
                </button>
              ))}
            </div>
            <div className="obs-filters">
              <label>
                Logger
                <select value={logLogger} onChange={(e) => setLogLogger(e.target.value)}>
                  <option value="">All loggers</option>
                  {loggerOptions.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grow">
                Search
                <input
                  value={logQuery}
                  placeholder="Filter message text…"
                  onChange={(e) => setLogQuery(e.target.value)}
                />
              </label>
            </div>
            <div className="obs-log-stream">
              {(logs.data?.logs || []).map((entry: any, i: number) => (
                <button
                  key={`${entry.ts}-${i}`}
                  type="button"
                  className={`obs-log-line level-${(entry.level || 'INFO').toLowerCase()}`}
                  onClick={() => setSelected({ kind: 'log', ...entry })}
                >
                  <span className="ts">{entry.ts?.replace('T', ' ').replace('+00:00', 'Z')}</span>
                  <span className="lvl">{entry.level}</span>
                  <span className="lg">{entry.logger}</span>
                  <span className="msg">{entry.message}</span>
                </button>
              ))}
              {!logs.data?.logs?.length && (
                <p className="muted">No matching log lines yet. Trigger a run to populate.</p>
              )}
            </div>
          </>
        )}

        {pane === 'database' && (
          <>
            <header className="obs-pane-head">
              <h2>Database · {table}</h2>
              <p className="muted small">
                Browse SQLite tables. Filter by run/job and search text columns. Click a row to inspect.
              </p>
            </header>
            <div className="obs-filters">
              <label>
                Table
                <select value={table} onChange={(e) => setTable(e.target.value)}>
                  {tables.map((name: string) => (
                    <option key={name} value={name}>
                      {name} ({tableCounts[name] ?? '—'})
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Run ID
                <select value={runId} onChange={(e) => setRunId(e.target.value)}>
                  <option value="">Any run</option>
                  {(overview.data?.recent_runs || []).map((r: any) => (
                    <option key={r.id} value={r.id}>
                      {r.id.slice(0, 8)}… · {r.status}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Job ID
                <input value={jobId} placeholder="optional" onChange={(e) => setJobId(e.target.value)} />
              </label>
              <label className="grow">
                Search
                <input
                  value={tableQuery}
                  placeholder="Text search across string columns…"
                  onChange={(e) => setTableQuery(e.target.value)}
                />
              </label>
            </div>
            <p className="muted small">
              Showing {db.data?.rows?.length ?? 0} of {db.data?.total ?? 0} rows
              {db.isFetching ? ' · refreshing…' : ''}
            </p>
            <div className="table-scroll">
              <table className="grid obs-db-grid">
                <thead>
                  <tr>
                    {(db.data?.columns || []).map((c: string) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(db.data?.rows || []).map((row: any, idx: number) => (
                    <tr
                      key={row.id || idx}
                      onClick={() => setSelected({ kind: 'row', table, ...row })}
                      className="clickable"
                    >
                      {(db.data?.columns || []).map((c: string) => (
                        <td key={c}>
                          {typeof row[c] === 'object' ? JSON.stringify(row[c]) : String(row[c] ?? '')}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        {pane === 'errors' && (
          <>
            <header className="obs-pane-head">
              <h2>Job errors</h2>
              <p className="muted small">Failed pipeline jobs with persisted error text.</p>
            </header>
            <div className="table-scroll">
              <table className="grid">
                <thead>
                  <tr>
                    <th>Updated</th>
                    <th>Drug</th>
                    <th>Step</th>
                    <th>Status</th>
                    <th>Error</th>
                    <th>Job</th>
                  </tr>
                </thead>
                <tbody>
                  {(overview.data?.recent_job_errors || []).map((j: any) => (
                    <tr
                      key={j.id}
                      className="clickable"
                      onClick={() => setSelected({ kind: 'job_error', ...j })}
                    >
                      <td>{j.updated_at}</td>
                      <td>{j.drug_name}</td>
                      <td>{j.current_step}</td>
                      <td>{j.status}</td>
                      <td className="error-cell">{j.error}</td>
                      <td>
                        <code>{j.id}</code>
                      </td>
                    </tr>
                  ))}
                  {!overview.data?.recent_job_errors?.length && (
                    <tr>
                      <td colSpan={6} className="muted">
                        No job errors recorded.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}

        {pane === 'runs' && (
          <>
            <header className="obs-pane-head">
              <h2>Recent runs</h2>
              <p className="muted small">Click a run to scope the database browser to that run_id.</p>
            </header>
            <div className="table-scroll">
              <table className="grid">
                <thead>
                  <tr>
                    <th>Created</th>
                    <th>Status</th>
                    <th>Jobs</th>
                    <th>Run ID</th>
                    <th>Error</th>
                  </tr>
                </thead>
                <tbody>
                  {(overview.data?.recent_runs || []).map((r: any) => (
                    <tr
                      key={r.id}
                      className="clickable"
                      onClick={() => {
                        setSelected({ kind: 'run', ...r })
                        setRunId(r.id)
                        setPane('database')
                        setTable('drug_jobs')
                      }}
                    >
                      <td>{r.created_at}</td>
                      <td>{r.status}</td>
                      <td>{r.job_count ?? '—'}</td>
                      <td>
                        <code>{r.id}</code>
                      </td>
                      <td>{r.error || '—'}</td>
                    </tr>
                  ))}
                  {!overview.data?.recent_runs?.length && (
                    <tr>
                      <td colSpan={5} className="muted">
                        No extraction runs yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      {selected && (
        <aside className="drawer obs-drawer" role="dialog" aria-label="Inspect selection">
          <div className="obs-drawer-head">
            <h3>Inspect</h3>
            <button type="button" className="close" onClick={() => setSelected(null)} aria-label="Close">
              ×
            </button>
          </div>
          <p className="muted small">Press Esc to close</p>
          <pre className="code">{JSON.stringify(selected, null, 2)}</pre>
        </aside>
      )}
    </div>
  )
}
