import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'

type Tab = 'profile' | 'quarters' | 'timeline'

export default function ProductDetailPage() {
  const { productId = '' } = useParams()
  const [tab, setTab] = useState<Tab>('profile')

  const q = useQuery({
    queryKey: ['product', productId],
    queryFn: () => api.getProduct(productId),
    enabled: !!productId,
  })

  if (q.isLoading) return <div className="page">Loading…</div>
  if (q.error) return <div className="page error">{(q.error as Error).message}</div>
  const product = q.data
  const queueCount = (product.quarters || []).filter((x: any) => x.in_queue).length +
    (product.missing_quarters || []).length

  return (
    <div className="page">
      <div className="crumb">
        <Link to="/">Library</Link> / {product.name}
      </div>

      <div className="identity">
        <div>
          <h1>
            {product.name} <span className="identity-generic">({product.generic})</span>
          </h1>
          <div className="muted">
            {[product.company, product.moa, product.indication].filter(Boolean).join(' · ')}
          </div>
          <div className="idchip">canonical_product_id: {product.id}</div>
        </div>
        <div className="stats">
          <div>
            <span>Cadence</span>
            <strong>{product.cadence === 'quarterly' ? 'Quarterly' : 'One-off'}</strong>
          </div>
          <div>
            <span>Coverage</span>
            <strong>{product.completeness_pct}%</strong>
          </div>
          <div>
            <span>Quarters</span>
            <strong>{(product.quarters || []).length}</strong>
          </div>
          <div>
            <span>In review queue</span>
            <strong>{queueCount}</strong>
          </div>
        </div>
      </div>

      <div className="tabs">
        <button className={tab === 'profile' ? 'active' : ''} onClick={() => setTab('profile')}>
          Profile
        </button>
        <button className={tab === 'quarters' ? 'active' : ''} onClick={() => setTab('quarters')}>
          Quarters
        </button>
        <button className={tab === 'timeline' ? 'active' : ''} onClick={() => setTab('timeline')}>
          Timeline
        </button>
        <Link className="tab-link" to={`/review?product=${product.id}`}>
          Review this product ({queueCount})
        </Link>
      </div>

      {tab === 'profile' && <ProfileTab product={product} />}
      {tab === 'quarters' && <QuartersTab product={product} />}
      {tab === 'timeline' && <TimelineTab product={product} />}
    </div>
  )
}

function ProfileTab({ product }: { product: any }) {
  // Values the pipeline derives onto the product itself rather than per quarter.
  const derived = [
    ['Canonical product', product.name],
    ['Active moiety', product.generic],
    ['Commercial owner', product.company],
    ['Regulatory sponsor', product.regulatory_sponsor],
    ['Application number', product.application_number],
    ['Approved indication', product.indication],
    ['Therapeutic area', product.therapeutic_area],
    ['Mechanism of action', product.moa],
    ['FDA EPC', (product.epc || []).join(', ') || null],
    ['Approved line of therapy', product.approved_lot],
    ['Formulation', product.dosage_form],
    ['Route', product.route],
    ['Initial FDA approval', product.initial_approval_date],
  ] as [string, string | null][]

  return (
    <>
      <div className="card-block">
        <h2>Product identity</h2>
        <p className="muted small">
          Mechanism of action and FDA Established Pharmacologic Class are stored
          separately — EPC is never used as a MOA fallback. Approved line of therapy comes
          only from explicit label wording; silence is recorded as
          all_lines_or_unspecified.
        </p>
        <table className="grid">
          <tbody>
            {derived.map(([label, value]) => (
              <tr key={label}>
                <th style={{ width: 220 }}>{label}</th>
                <td>{value || <span className="muted">unresolved</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card-block">
        <h2>Extracted profile fields</h2>
        <p className="muted small">
          One value per product, not per quarter. Editing one records it as a confirmed
          reviewer value, which outranks what a later scheduled run extracts.
        </p>
        <table className="grid">
          <thead>
            <tr>
              <th>Field</th>
              <th>Value</th>
              <th>Source</th>
              <th>Status</th>
              <th style={{ width: 60 }}></th>
            </tr>
          </thead>
          <tbody>
            {(product.profile || []).map((field: any) => (
              <ProfileFieldRow key={field.id} field={field} productId={product.id} />
            ))}
          </tbody>
        </table>
        {!(product.profile || []).length && (
          <p className="muted">No profile fields extracted yet.</p>
        )}
      </div>
    </>
  )
}

function ProfileFieldRow({ field, productId }: { field: any; productId: string }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(field.value ?? '')
  const [sourceUrl, setSourceUrl] = useState('')
  const [notes, setNotes] = useState('')

  const save = useMutation({
    mutationFn: () =>
      api.patchProfileField(field.id, {
        value,
        source_url: sourceUrl,
        reviewer_notes: notes || undefined,
      }),
    onSuccess: () => {
      setEditing(false)
      qc.invalidateQueries({ queryKey: ['product', productId] })
    },
  })

  const citation = field.citation || {}

  return (
    <tr>
      <td>{field.field}</td>
      <td>
        {field.value}
        {editing && (
          <div className="edit-form">
            <label>
              Value
              <input value={value} onChange={(e) => setValue(e.target.value)} />
            </label>
            <label>
              Source URL <span className="req">*</span>
              <input
                value={sourceUrl}
                placeholder="https://…"
                onChange={(e) => setSourceUrl(e.target.value)}
              />
            </label>
            <label>
              Note
              <input value={notes} onChange={(e) => setNotes(e.target.value)} />
            </label>
            {!sourceUrl.trim() && (
              <p className="gate-note">
                A reviewer-confirmed field still needs a citation before it can publish.
              </p>
            )}
            <div className="actions">
              <button
                disabled={!sourceUrl.trim() || !String(value).trim() || save.isPending}
                onClick={() => save.mutate()}
              >
                Save
              </button>
              <button className="ghost" onClick={() => setEditing(false)}>
                Cancel
              </button>
            </div>
            {save.error && <p className="error">{(save.error as Error).message}</p>}
          </div>
        )}
      </td>
      <td className="muted small">
        {citation.source_url ? (
          <a href={citation.source_url} target="_blank" rel="noreferrer">
            {citation.source_url}
          </a>
        ) : (
          '—'
        )}
      </td>
      <td>
        <span className={`pill ${field.validation_status === 'confirmed' ? 'sched' : 'ok'}`}>
          {field.validation_status}
        </span>
      </td>
      <td>
        {!editing && (
          <button className="linkish" onClick={() => setEditing(true)}>
            Edit
          </button>
        )}
      </td>
    </tr>
  )
}

function QuartersTab({ product }: { product: any }) {
  return (
    <>
      <div className="card-block">
        <h2>Quarterly series</h2>
        <table className="grid">
          <thead>
            <tr>
              <th>Period</th>
              <th>Value</th>
              <th>Scope</th>
              <th>Source and quote</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {(product.quarters || []).map((q: any) => (
              <tr key={q.id}>
                <td>
                  <strong>{q.period}</strong>
                </td>
                <td>
                  {q.value_normalized_usd_millions != null
                    ? `$${q.value_normalized_usd_millions}M`
                    : '—'}
                </td>
                <td>{q.revenue_scope}</td>
                <td>
                  <a href={q.source_url} target="_blank" rel="noreferrer">
                    {q.extraction_method}
                  </a>
                  <div className="quote">{q.source_quote}</div>
                </td>
                <td>
                  <span
                    className={`pill ${
                      q.validation_status === 'confirmed'
                        ? 'sched'
                        : q.in_queue
                          ? 'flagged'
                          : 'ok'
                    }`}
                  >
                    {q.in_queue ? `in queue · ${q.queue_reason}` : q.validation_status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!(product.quarters || []).length && <p className="muted">No quarters extracted yet.</p>}
      </div>

      {!!(product.missing_quarters || []).length && (
        <div className="card-block">
          <h2>Missing quarters</h2>
          <p className="muted small">
            Expected but not found. Resolve them from the{' '}
            <Link to={`/review?product=${product.id}`}>review queue</Link>.
          </p>
          <table className="grid">
            <thead>
              <tr>
                <th>Period</th>
                <th>Reason</th>
                <th>Recommended next step</th>
                <th>Confidence unavailable</th>
              </tr>
            </thead>
            <tbody>
              {product.missing_quarters.map((row: any) => (
                <tr key={row.id}>
                  <td>{row.period}</td>
                  <td>{row.reason_unresolved}</td>
                  <td>{row.recommended_next_step}</td>
                  <td>{row.confidence_that_unavailable}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

function TimelineTab({ product }: { product: any }) {
  return (
    <div className="card-block">
      <h2>Runs that touched this product</h2>
      <table className="grid">
        <thead>
          <tr>
            <th>When</th>
            <th>Run</th>
            <th>Status</th>
            <th>Step</th>
            <th>Completeness</th>
            <th>Auto-pass</th>
            <th>Needs review</th>
            <th>Unresolved</th>
          </tr>
        </thead>
        <tbody>
          {(product.timeline || []).map((entry: any) => (
            <tr key={entry.job_id}>
              <td>{new Date(entry.created_at).toLocaleString()}</td>
              <td>
                <code>{entry.run_id.slice(0, 8)}</code>
              </td>
              <td>{entry.status}</td>
              <td>{entry.current_step}</td>
              <td>{entry.completeness_pct}%</td>
              <td>{entry.auto_pass_count}</td>
              <td>{entry.needs_review_count}</td>
              <td>{entry.unresolved_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!(product.timeline || []).length && (
        <p className="muted">No runs recorded against this product yet.</p>
      )}
    </div>
  )
}
