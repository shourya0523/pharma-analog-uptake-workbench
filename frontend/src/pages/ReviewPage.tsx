import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api } from '../api/client'

export default function ReviewPage() {
  const { jobId = '' } = useParams()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['job', jobId], queryFn: () => api.getJob(jobId), enabled: !!jobId })

  if (q.isLoading) return <div className="page">Loading…</div>
  if (q.error) return <div className="page error">{(q.error as Error).message}</div>
  const job = q.data

  async function act(taskId: string, action: string) {
    await api.validationAction(taskId, { action })
    qc.invalidateQueries({ queryKey: ['job', jobId] })
  }

  return (
    <div className="page">
      <h1>Drug review — {job.drug_name}</h1>
      <p className="muted">
        {job.status} · step {job.current_step} · completeness {job.completeness_pct}%
      </p>

      <section>
        <h2>Product profile</h2>
        <table className="grid">
          <thead>
            <tr>
              <th>Field</th>
              <th>Value</th>
              <th>Source</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {job.profile.map((f: any, i: number) => (
              <tr key={i}>
                <td>{f.field}</td>
                <td>{f.value}</td>
                <td>
                  <a href={f.citation?.source_url} target="_blank" rel="noreferrer">
                    {f.citation?.source_url}
                  </a>
                  <div className="quote">{f.citation?.source_quote}</div>
                </td>
                <td>{f.validation_status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Quarterly revenue</h2>
        <table className="grid">
          <thead>
            <tr>
              <th>Period</th>
              <th>Value ($M)</th>
              <th>Scope</th>
              <th>Quote</th>
              <th>Confidence</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {job.datapoints.map((d: any) => (
              <tr key={d.id}>
                <td>{d.period}</td>
                <td>{d.value_normalized_usd_millions}</td>
                <td>{d.revenue_scope}</td>
                <td>
                  <a href={d.source_url} target="_blank" rel="noreferrer">
                    source
                  </a>
                  <div className="quote">{d.source_quote}</div>
                </td>
                <td>{d.confidence_score}</td>
                <td>{d.validation_status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Source audit log</h2>
        <table className="grid">
          <thead>
            <tr>
              <th>Type</th>
              <th>Title</th>
              <th>Retrieval</th>
              <th>Parsing</th>
              <th>Datapoints</th>
            </tr>
          </thead>
          <tbody>
            {job.sources.map((s: any) => (
              <tr key={s.source_id}>
                <td>{s.source_type}</td>
                <td>
                  <a href={s.source_url} target="_blank" rel="noreferrer">
                    {s.source_title || s.source_url}
                  </a>
                </td>
                <td>{s.retrieval_status}</td>
                <td>{s.parsing_status}</td>
                <td>{s.relevant_datapoints_found}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Unresolved quarters</h2>
        <table className="grid">
          <thead>
            <tr>
              <th>Period</th>
              <th>Reason</th>
              <th>Next step</th>
            </tr>
          </thead>
          <tbody>
            {job.unresolved_quarters.map((u: any) => (
              <tr key={u.id}>
                <td>{u.period}</td>
                <td>{u.reason_unresolved}</td>
                <td>{u.recommended_next_step}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Validation queue</h2>
        <table className="grid">
          <thead>
            <tr>
              <th>Datapoint</th>
              <th>Reason</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {job.validation_tasks.map((t: any) => (
              <tr key={t.id}>
                <td>{t.datapoint_id.slice(0, 8)}</td>
                <td>{t.reason}</td>
                <td>{t.status}</td>
                <td className="actions">
                  <button onClick={() => act(t.id, 'confirm')}>Confirm</button>
                  <button onClick={() => act(t.id, 'reject')}>Reject</button>
                  <button onClick={() => act(t.id, 'follow_up')}>Follow-up</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
