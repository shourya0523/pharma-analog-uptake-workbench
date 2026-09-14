import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api, type ReviewGroup, type ReviewItem } from '../api/client'

const PAGE_SIZE = 50

export default function ReviewQueuePage() {
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const productId = params.get('product') || ''
  const [itemType, setItemType] = useState('')
  const [reason, setReason] = useState('')
  const [offset, setOffset] = useState(0)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const q = useQuery({
    queryKey: ['review-queue', productId, itemType, reason, offset],
    queryFn: () =>
      api.reviewQueue({
        product_id: productId || undefined,
        item_type: itemType || undefined,
        reason: reason || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
  })

  const groups = q.data?.groups || []
  const groupsTotal = q.data?.groups_total ?? 0
  const pageEnd = Math.min(offset + groups.length, groupsTotal)

  function setProduct(value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set('product', value)
    else next.delete('product')
    setParams(next)
    setOffset(0)
  }

  function invalidate() {
    qc.invalidateQueries({ queryKey: ['review-queue'] })
    qc.invalidateQueries({ queryKey: ['products'] })
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
            could not. One row per product and quarter; the figures in question sit
            under it.
          </p>
        </div>
        <label className="filter-field">
          <span>Product</span>
          <select value={productId} onChange={(e) => setProduct(e.target.value)}>
            <option value="">All products</option>
            {(q.data?.products || []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="filter-field">
          <span>Item type</span>
          <select
            value={itemType}
            onChange={(e) => {
              setItemType(e.target.value)
              setOffset(0)
            }}
          >
            <option value="">Both</option>
            <option value="flagged">Flagged value</option>
            <option value="missing">Missing quarter</option>
          </select>
        </label>
        <label className="filter-field">
          <span>Flag reason</span>
          <select
            value={reason}
            onChange={(e) => {
              setReason(e.target.value)
              setOffset(0)
            }}
          >
            <option value="">Any reason</option>
            {Object.entries(q.data?.reasons || {}).map(([r, count]) => (
              <option key={r} value={r}>
                {r} ({count})
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
            setOffset(0)
          }}
        >
          Clear filters
        </button>
      </aside>

      <section className="dash-main">
        <div className="main-head">
          <div>
            <h1>{groupsTotal} questions to work</h1>
            <p className="muted small">
              Reasons come from the pipeline's own validation pass.
            </p>
          </div>
          <Pager
            from={groupsTotal ? offset + 1 : 0}
            to={pageEnd}
            total={groupsTotal}
            onPrev={offset > 0 ? () => setOffset(Math.max(0, offset - PAGE_SIZE)) : undefined}
            onNext={pageEnd < groupsTotal ? () => setOffset(offset + PAGE_SIZE) : undefined}
          />
        </div>

        {groups.map((group) => (
          <QueueGroup
            key={`${group.product_id || group.product}|${group.period}`}
            group={group}
            help={q.data?.reason_help || {}}
            expanded={expanded}
            onToggle={(id) => setExpanded({ ...expanded, [id]: !expanded[id] })}
            onResolved={invalidate}
          />
        ))}

        {!groups.length && (
          <div className="card-block">
            <p className="muted">Nothing is waiting on a person here.</p>
          </div>
        )}

        {groups.length > 0 && (
          <Pager
            from={offset + 1}
            to={pageEnd}
            total={groupsTotal}
            onPrev={offset > 0 ? () => setOffset(Math.max(0, offset - PAGE_SIZE)) : undefined}
            onNext={pageEnd < groupsTotal ? () => setOffset(offset + PAGE_SIZE) : undefined}
          />
        )}
      </section>
    </div>
  )
}

function Pager({
  from,
  to,
  total,
  onPrev,
  onNext,
}: {
  from: number
  to: number
  total: number
  onPrev?: () => void
  onNext?: () => void
}) {
  return (
    <div className="pager" aria-label="Queue pages">
      <button className="ghost" disabled={!onPrev} onClick={onPrev}>
        ← Previous
      </button>
      <span className="muted small">
        {from}–{to} of {total}
      </span>
      <button className="ghost" disabled={!onNext} onClick={onNext}>
        Next →
      </button>
    </div>
  )
}

/** A product and a quarter, with every open item about it beneath. */
function QueueGroup({
  group,
  help,
  expanded,
  onToggle,
  onResolved,
}: {
  group: ReviewGroup
  help: Record<string, string>
  expanded: Record<string, boolean>
  onToggle: (id: string) => void
  onResolved: () => void
}) {
  const single = group.items.length === 1
  return (
    <section className="queue-group">
      {!single && (
        <header className="queue-group-head">
          <strong>{group.product}</strong>
          <span className="muted">{group.period}</span>
          <span className="pill conf">{group.items.length} figures in question</span>
          {group.reasons.map((r) => (
            <span key={r} className="pill reason">
              {r}
            </span>
          ))}
        </header>
      )}
      {group.items.map((item) => (
        <QueueItem
          key={item.id}
          item={item}
          help={help[item.reason] || item.reason}
          expanded={!!expanded[item.id]}
          onToggle={() => onToggle(item.id)}
          onResolved={onResolved}
        />
      ))}
    </section>
  )
}

function QueueItem({
  item,
  help,
  expanded,
  onToggle,
  onResolved,
}: {
  item: ReviewItem
  /** Why it is in the queue, in the words of the stage that put it there. */
  help: string
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
            <div className="judge-note">{help}</div>
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
