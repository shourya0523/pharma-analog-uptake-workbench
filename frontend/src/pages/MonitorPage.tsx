import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'

export default function MonitorPage() {
  const { runId: paramId } = useParams()
  const runId = paramId || localStorage.getItem('lastRunId') || ''
  const q = useQuery({
    queryKey: ['run', runId],
    queryFn: () => api.getRun(runId),
    enabled: !!runId,
    refetchInterval: 3000,
  })

  if (!runId) return <div className="page">No run yet. Start one from Setup.</div>
  if (q.isLoading) return <div className="page">Loading…</div>
  if (q.error) return <div className="page error">{(q.error as Error).message}</div>
  const data = q.data

  return (
    <div className="page">
      <h1>Run monitor</h1>
      <p>
        Run <code>{data.id}</code> — {data.status}. Ready {data.aggregate.ready}/{data.aggregate.total}
      </p>
      <p>
        <Link to={`/dashboard/${data.id}`}>Open dashboard</Link> · <Link to="/export">Export</Link>
      </p>
      <table className="grid">
        <thead>
          <tr>
            <th>Drug</th>
            <th>Status</th>
            <th>Step</th>
            <th>Sources</th>
            <th>Candidates</th>
            <th>Auto-pass</th>
            <th>Needs review</th>
            <th>Unresolved</th>
            <th>Completeness</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {data.jobs.map((j: any) => (
            <tr key={j.id}>
              <td>{j.drug_name}</td>
              <td>{j.status}</td>
              <td>{j.current_step}</td>
              <td>{j.sources_found}</td>
              <td>{j.candidates_extracted}</td>
              <td>{j.auto_pass_count}</td>
              <td>{j.needs_review_count}</td>
              <td>{j.unresolved_count}</td>
              <td>{j.completeness_pct}%</td>
              <td>
                <Link to={`/review/${j.id}`}>Review</Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
