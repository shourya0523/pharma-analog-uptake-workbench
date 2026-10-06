import { useEffect, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api, type ReviewItem } from '../api/client'
import SourcePane, { type SourcePaneHandle } from './SourcePane'

const DONE_LABEL: Record<string, string> = {
  confirm: 'confirmed',
  reject: 'rejected',
  follow_up: 'sent for follow-up',
  edit: 'corrected',
}

/**
 * Flagged figures one at a time, each beside its stored source document.
 *
 * Keys: 1 confirm, E edit, U follow up, R reject, J / K next and previous,
 * / search the document, O open the original, Esc back to the queue.
 */
export default function ReviewFocus({
  items,
  start,
  help,
  onClose,
  onResolved,
}: {
  items: ReviewItem[]
  start: number
  help: Record<string, string>
  onClose: () => void
  onResolved: () => void
}) {
  const [index, setIndex] = useState(Math.min(start, Math.max(items.length - 1, 0)))
  const [done, setDone] = useState<Record<string, string>>({})
  // A correction in progress belongs to the figure it was started on, so
  // moving to another figure leaves it behind.
  const [draft, setDraft] = useState<{ id: string; value: string; scope: string; notes: string } | null>(null)
  const pane = useRef<SourcePaneHandle>(null)
  const item = items[index]
  const editing = !!item && draft?.id === item.id
  const startEdit = () =>
    setDraft({
      id: item.id,
      value: item.value_normalized_usd_millions != null ? String(item.value_normalized_usd_millions) : '',
      scope: item.revenue_scope || 'U.S.',
      notes: '',
    })
  const setField = (field: 'value' | 'scope' | 'notes', v: string) => setDraft((d) => (d ? { ...d, [field]: v } : d))

  const advance = () => setIndex((i) => Math.min(i + 1, items.length - 1))
  const finish = (action: string) => {
    setDone((d) => ({ ...d, [item.id]: action }))
    onResolved()
    advance()
  }
  const act = useMutation({
    mutationFn: (action: string) => api.validationAction(item.id, { action }),
    onSuccess: (_r, action) => finish(action),
  })
  const edit = useMutation({
    mutationFn: () =>
      api.patchDatapoint(item.datapoint_id as string, {
        value_normalized_usd_millions: Number(draft!.value),
        revenue_scope: draft!.scope,
        validation_status: 'confirmed',
        reviewer_notes: draft!.notes || undefined,
      }),
    onSuccess: () => finish('edit'),
  })
  const pending = act.isPending || edit.isPending
  const error = (act.error || edit.error) as Error | null

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement as HTMLElement)?.tagName)
      if (typing) {
        if (e.key === 'Escape') (document.activeElement as HTMLElement).blur()
        return
      }
      if (!item || pending) return
      const k = e.key.toLowerCase()
      if (k === '1' && !done[item.id]) act.mutate('confirm')
      else if (k === 'r' && !done[item.id]) act.mutate('reject')
      else if (k === 'u' && !done[item.id]) act.mutate('follow_up')
      else if (k === 'e' && !done[item.id]) startEdit()
      else if (k === 'j' || e.key === 'ArrowDown') setIndex((i) => Math.min(i + 1, items.length - 1))
      else if (k === 'k' || e.key === 'ArrowUp') setIndex((i) => Math.max(i - 1, 0))
      else if (k === '/') pane.current?.focusFind()
      else if (k === 'o') window.open(item.source_url, 'wb-source', 'noreferrer')
      else if (k === 'escape') onClose()
      else return
      e.preventDefault()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  })

  if (!item) {
    return (
      <div className="card-block">
        <p className="muted">No flagged figures on this page.</p>
        <button onClick={onClose}>Back to the queue</button>
      </div>
    )
  }

  const reported =
    item.value_reported != null && (item.currency !== 'USD' || (item.unit && item.unit !== 'millions'))
      ? `printed as ${item.value_reported.toLocaleString()} ${item.currency || ''} ${item.unit || ''}`.trim()
      : null
  const status = done[item.id]

  return (
    <div className="review-focus">
      <div className="rf-card">
        <div className="rf-nav">
          <button className="ghost" onClick={onClose}>← Queue</button>
          <span className="muted small">
            {index + 1} of {items.length} · {Object.keys(done).length} done
          </span>
          <span className="rf-spacer" />
          <button className="ghost" disabled={index === 0} onClick={() => setIndex(index - 1)}>
            Prev <kbd>K</kbd>
          </button>
          <button className="ghost" disabled={index === items.length - 1} onClick={() => setIndex(index + 1)}>
            Next <kbd>J</kbd>
          </button>
        </div>

        <h2 className="rf-product">{item.product}</h2>
        <div className="muted">
          {[item.period, item.revenue_scope, item.geography].filter(Boolean).join(' · ')}
        </div>
        <div className="rf-pills">
          <span className="pill flagged">flagged value</span>
          <span className="pill reason">{item.reason}</span>
          {item.confidence != null && <span className="pill conf">confidence {item.confidence.toFixed(2)}</span>}
          {status && <span className="pill ok">{DONE_LABEL[status]}</span>}
        </div>

        <div className="rf-value">
          {item.value_normalized_usd_millions != null ? `$${item.value_normalized_usd_millions}M` : '—'}
          {reported && <span className="muted small">{reported}</span>}
        </div>

        <div className="field">
          <div className="field-label">Source quote (must be verbatim in the cited document)</div>
          <div className="quote-block">{item.source_quote}</div>
          <div className="muted small">via {item.extraction_method}{item.reported_as ? ` · reported as ${item.reported_as}` : ''}</div>
        </div>
        <div className="field">
          <div className="field-label">Why it is in the queue</div>
          <div className="judge-note">{help[item.reason] || item.reason}</div>
        </div>
        {item.series_identity && (
          <div className="field">
            <div className="field-label">Series</div>
            <div className="small">{item.series_identity}{item.series_selection ? ` · ${item.series_selection}` : ''}</div>
          </div>
        )}

        {!editing ? (
          <div className="actions">
            <button disabled={pending || !!status} onClick={() => act.mutate('confirm')}>
              Confirm as-is <kbd>1</kbd>
            </button>
            <button className="ghost" disabled={pending || !!status} onClick={startEdit}>
              Edit value <kbd>E</kbd>
            </button>
            <button className="ghost" disabled={pending || !!status} onClick={() => act.mutate('follow_up')}>
              Follow up <kbd>U</kbd>
            </button>
            <button className="danger" disabled={pending || !!status} onClick={() => act.mutate('reject')}>
              Reject <kbd>R</kbd>
            </button>
          </div>
        ) : (
          <div className="edit-form">
            <label>
              Value, USD millions
              <input autoFocus value={draft!.value} onChange={(e) => setField('value', e.target.value)} />
            </label>
            <label>
              Revenue scope
              <select value={draft!.scope} onChange={(e) => setField('scope', e.target.value)}>
                <option>U.S.</option>
                <option>Worldwide</option>
                <option>ex-U.S.</option>
                <option>Product family</option>
              </select>
            </label>
            <label>
              Reviewer note
              <input value={draft!.notes} onChange={(e) => setField('notes', e.target.value)} />
            </label>
            <div className="actions">
              <button disabled={pending || !draft!.value.trim()} onClick={() => edit.mutate()}>
                Save as confirmed
              </button>
              <button className="ghost" onClick={() => setDraft(null)}>Cancel</button>
            </div>
          </div>
        )}
        {error && <p className="error">{error.message}</p>}

        <p className="rf-keys muted small">
          <kbd>1</kbd> confirm · <kbd>E</kbd> edit · <kbd>U</kbd> follow up · <kbd>R</kbd> reject ·{' '}
          <kbd>J</kbd>/<kbd>K</kbd> next/prev · <kbd>/</kbd> search document · <kbd>O</kbd> open original ·{' '}
          <kbd>Esc</kbd> queue
        </p>
      </div>

      <SourcePane
        ref={pane}
        row={{
          datapoint_id: item.datapoint_id as string,
          product: item.product,
          source_url: item.source_url || '',
          source_quote: item.source_quote,
          value_reported: item.value_reported,
          value_normalized_usd_millions: item.value_normalized_usd_millions,
          aliases: [item.reported_as],
        }}
      />
    </div>
  )
}
