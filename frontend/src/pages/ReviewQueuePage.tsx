import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api, type ReviewItem } from '../api/client'

/**
 * Prose for each reason, keyed by the reason the API sends.
 *
 * A snapshot of what `app/validation/sampling.py` attaches, plus the two kinds
 * the completeness stage records. It cannot be derived here - the producer is
 * Python - so a reason added there and not here falls back to showing the
 * reason itself, which is why the lookup below is guarded rather than indexed.
 * Serving the prose from the queue endpoint, next to the producer, would remove
 * this copy altogether.
 */
const REASON_HELP: Record<string, string> = {
  low_confidence: 'Confidence fell below the 0.7 gate.',
  conflict: 'Two candidates disagreed for this quarter and reconciliation picked one.',
  ocr_derived: 'Recovered from a PDF whose columns came from whitespace, not markup.',
  early_launch: 'One of the first quarters after launch, which are often restated.',
  recent_period: 'One of the two newest quarters, which are always sampled.',
  needs_review: 'The evidence judge did not accept the quote as supporting the value.',
  random_auto_pass_sample: 'A random QA sample of auto-passed rows.',
  interior_gap: 'A quarter between quarters that were extracted, so a value is expected.',
  not_disclosed: 'No product-level figure was found for this product at all.',
}

export default function ReviewQueuePage() {
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const productId = params.get('product') || ''
  const [itemType, setItemType] = useState('')
  const [reason, setReason] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const q = useQuery({
    queryKey: ['review-queue', productId, itemType, reason],
    queryFn: () =>
      api.reviewQueue({
        product_id: productId || undefined,
        item_type: itemType || undefined,
        reason: reason || undefined,
      }),
  })

  const items = q.data?.items || []
  const productNames = Array.from(
    new Map(items.map((i) => [i.product_id, i.product])).entries(),
  ).filter(([id]) => id) as [string, string][]

  function setProduct(value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set('product', value)
    else next.delete('product')
    setParams(next)
  }

  if (q.isLoading) return <div className="page">Loading review queue…</div>
  if (q.error) return <div className="page error">{(q.error as Error).message}</div>

  return (
    <div className="dash-layout">
      <aside className="filter-panel">
        <div className="filter-panel-head">
          <h2>Review queue</h2>
          <p className="filter-sub">
            Quarters the judge flagged, and quarters the pipeline expected to find but
            could not.
          </p>
        </div>
        <label className="filter-field">
          <span>Product</span>
          <select value={productId} onChange={(e) => setProduct(e.target.value)}>
            <option value="">All products</option>
            {productNames.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="filter-field">
          <span>Item type</span>
          <select value={itemType} onChange={(e) => setItemType(e.target.value)}>
            <option value="">Both</option>
            <option value="flagged">Flagged value</option>
            <option value="missing">Missing quarter</option>
          </select>
        </label>
        <label className="filter-field">
          <span>Flag reason</span>
          <select value={reason} onChange={(e) => setReason(e.target.value)}>
            <option value="">Any reason</option>
            {Array.from(new Set(items.map((i) => i.reason)))
              .sort()
              .map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
          </select>
        </label>
        <div className="filter-count">
          {q.data?.total ?? 0} open
          <br />
          {q.data?.flagged ?? 0} flagged values
          <br />
          {q.data?.missing ?? 0} missing quarters
        </div>
        <button
          className="filter-clear"
          onClick={() => {
            setProduct('')
            setItemType('')
            setReason('')
          }}
        >
          Clear filters
        </button>
      </aside>

      <section className="dash-main">
        <div className="main-head">
          <div>
            <h1>{items.length} items to work</h1>
            <p className="muted small">
              Reasons come from the pipeline's own validation pass.
            </p>
          </div>
        </div>

        {items.map((item) => (
          <QueueItem
            key={item.id}
            item={item}
            expanded={!!expanded[item.id]}
            onToggle={() => setExpanded({ ...expanded, [item.id]: !expanded[item.id] })}
            onResolved={() => {
              qc.invalidateQueries({ queryKey: ['review-queue'] })
              qc.invalidateQueries({ queryKey: ['products'] })
            }}
          />
        ))}

        {!items.length && (
          <div className="card-block">
            <p className="muted">Nothing is waiting on a person here.</p>
          </div>
        )}
      </section>
    </div>
  )
}

function QueueItem({
  item,
  expanded,
  onToggle,
  onResolved,
}: {
  item: ReviewItem
  expanded: boolean
  onToggle: () => void
  onResolved: () => void
}) {
  const isFlagged = item.type === 'flagged'
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(
    item.value_normalized_usd_millions != null
      ? String(item.value_normalized_usd_millions)
      : '',
  )
  const [scope, setScope] = useState(item.revenue_scope || 'U.S.')
  const [sourceUrl, setSourceUrl] = useState('')
  const [notes, setNotes] = useState('')

  const flaggedAction = useMutation({
    mutationFn: (action: string) => api.validationAction(item.id, { action }),
    onSuccess: onResolved,
  })
  const editValue = useMutation({
    mutationFn: () =>
      api.patchDatapoint(item.datapoint_id as string, {
        value_normalized_usd_millions: Number(value),
        revenue_scope: scope,
        validation_status: 'confirmed',
        reviewer_notes: notes || undefined,
      }),
    onSuccess: () => {
      setEditing(false)
      onResolved()
    },
  })
  const missingAction = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.resolveUnresolved(item.id, body),
    onSuccess: () => {
      setEditing(false)
      onResolved()
    },
  })

  const pending =
    flaggedAction.isPending || editValue.isPending || missingAction.isPending
  const error = (flaggedAction.error || editValue.error || missingAction.error) as
    | Error
    | undefined

  // The export gate refuses a confirmed value with no citation, so the form does too.
  const citationMissing = !sourceUrl.trim()
  const saveDisabled = pending || !value.trim() || (!isFlagged && citationMissing)

  return (
    <article className={`queue-item ${isFlagged ? '' : 'missing'}`}>
      <header onClick={onToggle} className="queue-item-head">
        <strong>{item.product}</strong>
        <span className="muted">{item.period}</span>
        <span className={`pill ${isFlagged ? 'flagged' : 'missing'}`}>
          {isFlagged ? 'flagged value' : 'missing quarter'}
        </span>
        <span className="pill reason">{item.reason}</span>
        <span className="pill conf">
          {isFlagged ? 'confidence' : 'unavailable'} {item.confidence?.toFixed(2)}
        </span>
        <span className="chev">{expanded ? '−' : '+'}</span>
      </header>

      {expanded && (
        <div className="queue-item-body">
          {isFlagged ? (
            <>
              <div className="value-row">
                <span className="val">
                  {item.value_normalized_usd_millions != null
                    ? `$${item.value_normalized_usd_millions}M`
                    : '—'}
                </span>
                <span className="muted">{item.revenue_scope}</span>
              </div>
              <Field label="Source">
                <a href={item.source_url} target="_blank" rel="noreferrer">
                  {item.source_url}
                </a>
                <div className="muted small">via {item.extraction_method}</div>
              </Field>
              <Field label="Source quote (must be verbatim in the cited document)">
                <div className="quote-block">{item.source_quote}</div>
              </Field>
            </>
          ) : (
            <>
              <Field label="Why it is unresolved">
                <div className="judge-note">{item.reason_unresolved}</div>
              </Field>
              <Field label="Sources already checked">
                <ul className="url-list">
                  {(item.sources_checked || []).map((u) => (
                    <li key={u}>{u}</li>
                  ))}
                </ul>
              </Field>
              <Field label="Recommended next step">
                <div className="quote-block">{item.recommended_next_step}</div>
              </Field>
            </>
          )}

          <Field label="Why it is in the queue">
            <div className="judge-note">{REASON_HELP[item.reason] || item.reason}</div>
          </Field>

          {!editing && (
            <div className="actions">
              {isFlagged ? (
                <>
                  <button disabled={pending} onClick={() => flaggedAction.mutate('confirm')}>
                    Confirm as-is
                  </button>
                  <button className="ghost" onClick={() => setEditing(true)}>
                    Edit value
                  </button>
                  <button
                    className="ghost"
                    disabled={pending}
                    onClick={() => flaggedAction.mutate('follow_up')}
                  >
                    Follow up
                  </button>
                  <button
                    className="danger"
                    disabled={pending}
                    onClick={() => flaggedAction.mutate('reject')}
                  >
                    Reject
                  </button>
                </>
              ) : (
                <>
                  <button onClick={() => setEditing(true)}>Enter value manually</button>
                  <button
                    className="ghost"
                    disabled={pending}
                    onClick={() => missingAction.mutate({ action: 'not_disclosed' })}
                  >
                    Confirm not disclosed
                  </button>
                  <button
                    className="ghost"
                    disabled={pending}
                    onClick={() => missingAction.mutate({ action: 're_queue' })}
                  >
                    Re-queue
                  </button>
                </>
              )}
            </div>
          )}

          {editing && (
            <div className="edit-form">
              <label>
                Value, USD millions
                <input value={value} onChange={(e) => setValue(e.target.value)} />
              </label>
              <label>
                Revenue scope
                <select value={scope} onChange={(e) => setScope(e.target.value)}>
                  <option>U.S.</option>
                  <option>Worldwide</option>
                  <option>ex-U.S.</option>
                  <option>Product family</option>
                </select>
              </label>
              <label>
                Source URL {!isFlagged && <span className="req">*</span>}
                <input
                  value={sourceUrl}
                  placeholder="https://www.sec.gov/Archives/…"
                  onChange={(e) => setSourceUrl(e.target.value)}
                />
              </label>
              <label>
                Reviewer note
                <input value={notes} onChange={(e) => setNotes(e.target.value)} />
              </label>
              {!isFlagged && citationMissing && (
                <p className="gate-note">
                  A confirmed value cannot publish without a citation — the export gate
                  rejects confirmed datapoints that have no source URL.
                </p>
              )}
              <div className="actions">
                <button
                  disabled={saveDisabled}
                  onClick={() =>
                    isFlagged
                      ? editValue.mutate()
                      : missingAction.mutate({
                          action: 'enter_value',
                          value_normalized_usd_millions: Number(value),
                          revenue_scope: scope,
                          source_url: sourceUrl,
                          reviewer_notes: notes || undefined,
                        })
                  }
                >
                  Save as confirmed
                </button>
                <button className="ghost" onClick={() => setEditing(false)}>
                  Cancel
                </button>
              </div>
            </div>
          )}

          {error && <p className="error">{error.message}</p>}
        </div>
      )}
    </article>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <div className="field-label">{label}</div>
      {children}
    </div>
  )
}
