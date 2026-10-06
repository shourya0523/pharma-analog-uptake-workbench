import { useEffect, useImperativeHandle, useRef, useState, type Ref } from 'react'
import { API } from '../api/client'
import { SourcePreview, type PreviewRow, type PreviewStatus } from './sourcePreview'

export type SourcePaneHandle = { focusFind: () => void }

const STATUS_TEXT: Record<PreviewStatus['state'], (s: any) => string> = {
  loading: () => 'Loading the stored copy…',
  searching: (s) => `Looking for the figure… page ${s.page} of ${s.of}`,
  found: (s) => `Figure found${s.page ? ` on page ${s.page}` : ''} and highlighted`,
  beneath: () =>
    "Figure found on an unlabelled line under the product's lines; check it is the product's total",
  line: (s) =>
    `Product line found${s.page ? ` on page ${s.page}` : ''}; the exact figure was not matched, so check the column`,
  quote: (s) => `Quoted passage found${s.page ? ` on page ${s.page}` : ''} and highlighted`,
  'label-only': (s) => `Figure not found; ${s.count} mentions of the product are highlighted`,
  'not-found': () => 'Figure not found automatically. Search the document (/) or use the quote',
  failed: (s) => `Not shown: ${s.message}`,
}
const TONE: Partial<Record<PreviewStatus['state'], string>> = {
  found: 'ok', quote: 'ok', beneath: 'warn', line: 'warn', 'label-only': 'warn', 'not-found': 'warn', failed: 'warn',
}

/** The stored source document of one figure, with the figure found and marked. */
export default function SourcePane({ row, ref }: { row: PreviewRow; ref?: Ref<SourcePaneHandle> }) {
  const host = useRef<HTMLDivElement>(null)
  const preview = useRef<SourcePreview | null>(null)
  const findBox = useRef<HTMLInputElement>(null)
  const [status, setStatus] = useState<PreviewStatus>({ state: 'loading' })
  const [findNote, setFindNote] = useState('')
  // A search belongs to the document it was typed against.
  const [search, setSearch] = useState({ id: '', text: '' })
  const query = search.id === row.datapoint_id ? search.text : ''
  const setQuery = (text: string) => setSearch({ id: row.datapoint_id, text })

  useImperativeHandle(ref, () => ({
    focusFind: () => { findBox.current?.focus(); findBox.current?.select() },
  }))

  useEffect(() => {
    if (!host.current) return
    // A search result stays until another document starts loading, even if the
    // locator reports in after it.
    const p = new SourcePreview(host.current, API, (s) => {
      setStatus(s)
      if (s.state === 'loading') setFindNote('')
    })
    preview.current = p
    return () => p.destroy()
  }, [])

  useEffect(() => {
    preview.current?.show(row)
  }, [row.datapoint_id]) // eslint-disable-line react-hooks/exhaustive-deps

  async function runFind(step: number) {
    const r = await preview.current!.find(query, step)
    setFindNote(r.count ? `${r.index} of ${r.count}${r.unit ? ` ${r.unit}` : ''} for “${query}”` : `No match for “${query}”`)
  }

  return (
    <div className="source-pane">
      <div className="sp-bar">
        <span className={`sp-status ${findNote ? '' : TONE[status.state] || ''}`} role="status">
          {findNote || STATUS_TEXT[status.state](status)}
        </span>
        <input
          ref={findBox}
          className="sp-find"
          value={query}
          placeholder="Search the document  /"
          aria-label="Search the document"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); runFind(e.shiftKey ? -1 : 1) }
            if (e.key === 'Escape') e.currentTarget.blur()
          }}
        />
        <a className="sp-open" href={row.source_url} target="wb-source" rel="noreferrer">
          Open original ↗
        </a>
      </div>
      <div className="sp-body" ref={host} />
    </div>
  )
}
