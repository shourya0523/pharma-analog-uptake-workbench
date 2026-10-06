// Shows a figure's source document inside the review screen and points at the
// figure in it.
//
// The document is the copy the pipeline stored (GET /datapoints/{id}/source),
// so it is what the extractor read. HTML is shown in a script-less sandboxed
// iframe; PDFs are drawn with pdf.js. The figure is located from what the row
// itself says: the product's names and the number as the source printed it.

import type { PDFDocumentProxy, PDFPageProxy, PageViewport } from 'pdfjs-dist'

export type PreviewRow = {
  datapoint_id: string
  product: string
  source_url: string
  source_quote?: string | null
  value_reported?: number | null
  value_normalized_usd_millions?: number | null
  /** Other names the document may print the product under. */
  aliases?: (string | null | undefined)[]
}

export type PreviewStatus =
  | { state: 'loading' }
  | { state: 'searching'; page: number; of: number }
  | { state: 'found' | 'line' | 'quote' | 'beneath'; page?: number }
  | { state: 'label-only'; count: number }
  | { state: 'not-found' }
  | { state: 'failed'; message: string }

type Fetched =
  | { type: 'pdf'; bytes: Uint8Array; finalUrl: string }
  | { type: 'html' | 'text'; text: string; finalUrl: string }

type Targets = {
  names: string[]
  hasName: (text: string) => boolean
  primary: RegExp | null
  secondary: RegExp | null
  pieces: string[]
}

// ---------------------------------------------------------------- matching

export const norm = (s: string | null | undefined) =>
  (s || '').replace(/[®™*†‡]/g, '').replace(/\s+/g, ' ').trim().toLowerCase()
const squash = (s: string) => norm(s).replace(/\s+/g, '')
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** The ways a document may print a number: 1234.5 as 1,234.5 / 1234.5 / 1 234.5 / 1.234,5. */
export function figureVariants(value: number | null | undefined): string[] {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return []
  const n = Math.abs(Number(value))
  const out = new Set<string>()
  for (const dp of [0, 1, 2, 3]) {
    const scaled = n * 10 ** dp
    if (Math.abs(scaled - Math.round(scaled)) > 1e-6) continue
    const [int, frac] = n.toFixed(dp).split('.')
    const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
    for (const body of [int, grouped, grouped.replace(/,/g, ' '), grouped.replace(/,/g, ' ')]) {
      out.add(frac ? `${body}.${frac}` : body)
    }
    out.add(frac ? `${grouped.replace(/,/g, '.')},${frac}` : grouped.replace(/,/g, '.'))
  }
  return [...out].filter(Boolean)
}

/** Numbers printed in the quote, for derived figures whose result the document does not print. */
function quoteNumbers(quote: string): string[] {
  return (quote.match(/\d[\d,.  ]*\d|\d/g) || [])
    .map((t) => t.trim())
    .filter((t) => t && !/^(19|20)\d\d$/.test(t))
}

/** Any of the strings as a whole number: not part of a longer one. */
function figureRegex(strings: string[]): RegExp | null {
  const parts = [...new Set(strings)].sort((a, b) => b.length - a.length).map(escapeRe)
  if (!parts.length) return null
  return new RegExp(`(?<![\\d.,])(?:${parts.join('|')})(?![\\d]|[.,]\\d)`, 'g')
}

/** Verbatim-looking pieces of the quote (between pipes), long enough to be distinctive. */
function quotePieces(quote: string): string[] {
  return quote.split('|').map((p) => p.trim()).filter((p) => p.length >= 12 && p.length <= 160)
}

export function targetsFor(row: PreviewRow): Targets {
  const first = (row.product || '').split(/[\s/]+/)[0]
  const names = [
    ...new Set(
      [row.product, ...(row.aliases || []), first.length >= 5 ? first : null]
        .map(norm)
        .filter((n) => n && n.length >= 3),
    ),
  ]
  // The number as printed when the pipeline kept it; the USD figure is the
  // same number when the source reported USD millions.
  const printed = figureVariants(row.value_reported ?? row.value_normalized_usd_millions)
  return {
    names,
    hasName: (text) => names.some((n) => text.includes(n)),
    primary: figureRegex(printed),
    secondary: figureRegex([...printed, ...quoteNumbers(row.source_quote || '')]),
    pieces: quotePieces(row.source_quote || '').map(norm),
  }
}

// ---------------------------------------------------------------- HTML

const HIGHLIGHT_CSS = `
  mark.wb-fig { background: #fde68a; color: #000; outline: 2px solid #b45309; border-radius: 2px; }
  mark.wb-label { background: #dbeafe; color: #000; border-radius: 2px; }
  mark.wb-find { background: #fef3c7; color: #000; }
  mark.wb-find.wb-current { background: #fb923c; }
  .wb-row { outline: 3px solid #1d4ed8 !important; outline-offset: 1px; background: #eff6ff !important; }
`

/** Text with its text nodes joined by spaces: table cells sit side by side with
 * no whitespace between them, so a row "Calderon | 240 | 8 | 248" reads
 * "Calderon2408248" from textContent and no figure in it matches. */
export function spacedText(el: Element): string {
  const walker = el.ownerDocument.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  const parts: string[] = []
  while (walker.nextNode()) parts.push(walker.currentNode.nodeValue || '')
  return parts.join(' ').replace(/ /g, ' ')
}

function markText(root: Element, regex: RegExp | null, cls: string, limit = 50): HTMLElement[] {
  if (!regex) return []
  const doc = root.ownerDocument
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const nodes: Text[] = []
  while (walker.nextNode()) nodes.push(walker.currentNode as Text)
  const marks: HTMLElement[] = []
  for (const node of nodes) {
    if (marks.length >= limit) break
    const text = node.nodeValue || ''
    regex.lastIndex = 0
    const frag = doc.createDocumentFragment()
    let m: RegExpExecArray | null
    let last = 0
    let hit = false
    while ((m = regex.exec(text)) && marks.length < limit) {
      if (m[0] === '') { regex.lastIndex++; continue }
      hit = true
      frag.append(text.slice(last, m.index))
      const mark = doc.createElement('mark')
      mark.className = cls
      mark.textContent = m[0]
      frag.append(mark)
      marks.push(mark)
      last = m.index + m[0].length
    }
    if (hit) { frag.append(text.slice(last)); node.replaceWith(frag) }
  }
  return marks
}

function namesRegex(names: string[]): RegExp | null {
  if (!names.length) return null
  const one = (n: string) => n.split(' ').map(escapeRe).join('[\\s\\u00a0]*[®™*]?[\\s\\u00a0]*')
  return new RegExp(names.map(one).join('|'), 'gi')
}

export type HtmlHit = { el: Element; exact: boolean; beneath?: Element }

/** The row (or text block) carrying the product's name and the figure. When the
 * named line lacks it, the few rows beneath: a product printed as regional
 * lines ("Calderon - U.S.", "Europe", ...) often totals on an unlabelled line. */
export function locateInHtml(doc: Document, t: Targets): HtmlHit | null {
  const squashedNames = t.names.map((n) => n.replace(/\s+/g, ''))
  const named = (el: Element) => {
    const raw = el.textContent || ''
    return raw.length <= 4000 && squashedNames.some((n) => squash(raw).includes(n))
  }
  const has = (el: Element, rx: RegExp) => { rx.lastIndex = 0; return rx.test(spacedText(el)) }
  const tableRows = [...doc.querySelectorAll('tr')].filter(named)
  const blocks = [...doc.querySelectorAll('p, li, td, div:not(:has(div,p,table))')].filter(named)
  for (const rx of [t.primary, t.secondary]) {
    if (!rx) continue
    const hit = tableRows.find((el) => has(el, rx)) || blocks.find((el) => has(el, rx))
    if (hit) return { el: hit, exact: rx === t.primary }
  }
  if (t.primary) {
    for (const el of tableRows) {
      let next = el.nextElementSibling
      for (let step = 0; next && step < 5; step += 1, next = next.nextElementSibling) {
        if (next.tagName === 'TR' && has(next, t.primary)) return { el: next, exact: true, beneath: el }
      }
    }
  }
  return null
}

/** The smallest element whose text contains one of the quote's pieces. */
export function locateQuoteInHtml(doc: Document, t: Targets): Element | null {
  const elements = [...doc.querySelectorAll('p, li, td, tr, h1, h2, h3, h4, div, span')]
  for (const piece of t.pieces) {
    const squashed = piece.replace(/\s+/g, '')
    let best: Element | null = null
    for (const el of elements) {
      const raw = el.textContent || ''
      if (raw.length > 20000 || (best && raw.length >= (best.textContent || '').length)) continue
      if (!squash(raw).includes(squashed)) continue
      if (norm(spacedText(el)).includes(piece)) best = el
    }
    if (best) return best
  }
  return null
}

function stripActive(html: string, baseUrl: string): string {
  const parsed = new DOMParser().parseFromString(html, 'text/html')
  parsed.querySelectorAll('script, noscript, iframe, object, embed, meta[http-equiv]').forEach((n) => n.remove())
  parsed.querySelectorAll('*').forEach((el) => {
    for (const a of [...el.attributes]) if (/^on/i.test(a.name)) el.removeAttribute(a.name)
  })
  const base = parsed.createElement('base')
  base.href = baseUrl
  base.target = '_blank'
  parsed.head.prepend(base)
  const style = parsed.createElement('style')
  style.textContent = HIGHLIGHT_CSS
  parsed.head.append(style)
  return '<!doctype html>' + parsed.documentElement.outerHTML
}

/** Scroll only the document pane (never the page) so el sits mid-view; sideways
 * only when it would otherwise be off screen. */
/**
 * Undo whatever keeps el out of sight in a source page: a collapsed "read
 * more" section, a closed <details>, a hidden attribute. The page's own button
 * for it needs the scripts the preview removes.
 */
export function reveal(el: Element) {
  const win = el.ownerDocument.defaultView
  if (!win) return
  for (let node: Element | null = el; node; node = node.parentElement) {
    if (node instanceof win.HTMLDetailsElement) node.open = true
    node.removeAttribute('hidden')
    const style = win.getComputedStyle(node)
    if (style.display === 'none') (node as HTMLElement).style.setProperty('display', 'block', 'important')
    if (style.visibility === 'hidden') (node as HTMLElement).style.setProperty('visibility', 'visible', 'important')
  }
}

function centerIn(el: Element) {
  reveal(el)
  const doc = el.ownerDocument
  const r = el.getBoundingClientRect()
  if (doc !== document) {
    const win = doc.defaultView
    if (!win) return
    const left = r.right + win.scrollX <= win.innerWidth ? 0 : Math.max(0, win.scrollX + r.left - 40)
    win.scrollTo({ top: win.scrollY + r.top - win.innerHeight / 2 + r.height / 2, left })
    return
  }
  const pane = el.closest('.sp-body')
  if (!pane) return
  const p = pane.getBoundingClientRect()
  pane.scrollTop += r.top - p.top - pane.clientHeight / 2 + r.height / 2
}

// ---------------------------------------------------------------- fetching

const cache = new Map<string, Promise<Fetched>>()

export function fetchSource(apiBase: string, datapointId: string): Promise<Fetched> {
  const existing = cache.get(datapointId)
  if (existing) return existing
  const p = (async (): Promise<Fetched> => {
    const res = await fetch(`${apiBase}/datapoints/${encodeURIComponent(datapointId)}/source`)
    if (!res.ok) {
      let reason = `${res.status}`
      try { reason = (await res.json()).detail || reason } catch { /* not JSON */ }
      throw new Error(reason)
    }
    const type = (res.headers.get('x-source-content-type') || res.headers.get('content-type') || '').toLowerCase()
    const finalUrl = res.headers.get('x-source-url') || ''
    const bytes = new Uint8Array(await res.arrayBuffer())
    const isPdf = type.includes('pdf') || (bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46)
    if (isPdf) return { type: 'pdf', bytes, finalUrl }
    const charset = /charset=([\w-]+)/.exec(type)?.[1] || 'utf-8'
    let text: string
    try { text = new TextDecoder(charset).decode(bytes) } catch { text = new TextDecoder().decode(bytes) }
    const looksHtml = /<(html|body|table|div)[\s>]/i.test(text.slice(0, 20000))
    return { type: type.includes('text/plain') && !looksHtml ? 'text' : 'html', text, finalUrl }
  })()
  cache.set(datapointId, p)
  p.catch(() => cache.delete(datapointId))
  return p
}

// ---------------------------------------------------------------- PDF

type TextItem = { str: string; transform: number[]; width: number; height: number }
type Line = { items: TextItem[]; text: string }

let pdfjsPromise: Promise<typeof import('pdfjs-dist')> | null = null
function pdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = Promise.all([
      import('pdfjs-dist'),
      import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
    ]).then(([lib, worker]) => {
      lib.GlobalWorkerOptions.workerSrc = worker.default
      return lib
    })
  }
  return pdfjsPromise
}

async function pageLines(page: PDFPageProxy): Promise<Line[]> {
  const content = await page.getTextContent()
  const lines = new Map<number, TextItem[]>()
  for (const raw of content.items as TextItem[]) {
    if (!raw.str || !raw.str.trim()) continue
    const key = Math.round(raw.transform[5] / 3)
    if (!lines.has(key)) lines.set(key, [])
    lines.get(key)!.push(raw)
  }
  return [...lines.values()].map((items) => {
    items.sort((a, b) => a.transform[4] - b.transform[4])
    return { items, text: items.map((i) => i.str).join(' ') }
  })
}

/** For each line naming the product, every item printed level with it: a table
 * row whose label and numbers sit at slightly different heights. */
function rowBands(lines: Line[], t: Targets): Line[] {
  const items = lines.flatMap((l) => l.items)
  const bands: Line[] = []
  for (const line of lines) {
    if (!t.hasName(norm(line.text))) continue
    const y = line.items[0].transform[5]
    const tol = Math.max(3, (line.items[0].height || 8) * 0.6)
    const level = items.filter((i) => Math.abs(i.transform[5] - y) <= tol).sort((a, b) => a.transform[4] - b.transform[4])
    bands.push({ items: level, text: level.map((i) => i.str).join(' ') })
  }
  return bands
}

// ---------------------------------------------------------------- the pane

export class SourcePreview {
  private token = 0
  private mode: 'html' | 'pdf' | null = null
  private frame: HTMLIFrameElement | null = null
  private pdf: PDFDocumentProxy | null = null
  private lines = new Map<number, Promise<Line[]>>()
  private page = 1
  private findQuery: string | null = null
  private findMarks: HTMLElement[] = []
  private findHits: number[] = []
  private findIndex = -1
  private host: HTMLElement
  private apiBase: string
  private onStatus: (s: PreviewStatus) => void

  constructor(host: HTMLElement, apiBase: string, onStatus: (s: PreviewStatus) => void) {
    this.host = host
    this.apiBase = apiBase
    this.onStatus = onStatus
  }

  destroy() {
    this.token += 1
    this.pdf?.destroy()
  }

  async show(row: PreviewRow) {
    const token = ++this.token
    this.mode = null
    this.findQuery = null
    this.host.innerHTML = '<div class="sp-empty">Loading the stored copy…</div>'
    this.onStatus({ state: 'loading' })
    let doc: Fetched
    try {
      doc = await fetchSource(this.apiBase, row.datapoint_id)
    } catch (err) {
      if (token !== this.token) return
      const message = (err as Error).message
      this.host.innerHTML = ''
      const box = document.createElement('div')
      box.className = 'sp-empty'
      box.textContent = `The document cannot be shown here: ${message}.`
      this.host.append(box)
      this.onStatus({ state: 'failed', message })
      return
    }
    if (token !== this.token) return
    if (doc.type === 'pdf') return this.showPdf(doc.bytes, row, token)
    return this.showHtml(doc, row, token)
  }

  private showHtml(doc: Extract<Fetched, { type: 'html' | 'text' }>, row: PreviewRow, token: number) {
    this.mode = 'html'
    const iframe = document.createElement('iframe')
    iframe.className = 'sp-frame'
    iframe.setAttribute('sandbox', 'allow-same-origin allow-popups allow-popups-to-escape-sandbox')
    iframe.setAttribute('referrerpolicy', 'no-referrer')
    iframe.title = 'Source document'
    const escaped = doc.text.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c] as string)
    const html = doc.type === 'text' ? `<pre style="white-space:pre-wrap;font:13px/1.5 monospace">${escaped}</pre>` : doc.text
    iframe.srcdoc = stripActive(html, doc.finalUrl || row.source_url)
    this.host.replaceChildren(iframe)
    this.frame = iframe
    iframe.addEventListener('load', () => {
      if (token !== this.token || !iframe.contentDocument) return
      const d = iframe.contentDocument
      const t = targetsFor(row)
      const found = locateInHtml(d, t)
      const quoted = found ? null : locateQuoteInHtml(d, t)
      if (found) {
        found.el.classList.add('wb-row')
        markText(found.beneath || found.el, namesRegex(t.names), 'wb-label', 3)
        const figs = markText(found.el, found.exact ? t.primary : t.secondary, 'wb-fig', 8)
        centerIn(figs[0] || found.el)
        this.onStatus({ state: found.beneath ? 'beneath' : found.exact ? 'found' : 'line' })
      } else if (quoted) {
        quoted.classList.add('wb-row')
        markText(quoted, namesRegex(t.names), 'wb-label', 5)
        centerIn(quoted)
        this.onStatus({ state: 'quote' })
      } else {
        const labels = markText(d.body, namesRegex(t.names), 'wb-label', 200)
        if (labels[0]) centerIn(labels[0])
        this.onStatus(labels.length ? { state: 'label-only', count: labels.length } : { state: 'not-found' })
      }
    }, { once: true })
  }

  private async showPdf(bytes: Uint8Array, row: PreviewRow, token: number) {
    this.mode = 'pdf'
    this.host.innerHTML = `<div class="sp-pdf"><div class="sp-pdfbar">
        <button class="ghost" data-sp="prev">‹ Page</button><span class="sp-pageno muted small"></span>
        <button class="ghost" data-sp="next">Page ›</button></div><div class="sp-pages"></div></div>`
    ;(this.host.querySelector('[data-sp="prev"]') as HTMLElement).onclick = () => this.goPage(this.page - 1)
    ;(this.host.querySelector('[data-sp="next"]') as HTMLElement).onclick = () => this.goPage(this.page + 1)
    const lib = await pdfjs()
    // pdf.js takes ownership of the buffer it is given, so it gets a copy.
    const pdf = await lib.getDocument({ data: bytes.slice(), isEvalSupported: false }).promise
    if (token !== this.token) { pdf.destroy(); return }
    this.pdf = pdf
    this.lines = new Map()
    const t = targetsFor(row)
    // The exact figure on any page, then any number the quote prints, then a
    // verbatim piece of the quote.
    const passes = ([[t.primary, 'found'], [t.secondary, 'line']] as const).filter(([rx]) => rx)
    let best: { page: number; line: Line; rx: RegExp | null; state: 'found' | 'line' | 'quote' } | null = null
    for (const [rx, state] of passes) {
      for (let n = 1; n <= pdf.numPages && !best; n++) {
        if (token !== this.token) return
        this.onStatus({ state: 'searching', page: n, of: pdf.numPages })
        const band = rowBands(await this.linesOf(n), t).find((b) => { rx!.lastIndex = 0; return rx!.test(b.text) })
        if (band) best = { page: n, line: band, rx, state }
      }
      if (best) break
    }
    for (let n = 1; n <= pdf.numPages && !best && t.pieces.length; n++) {
      const line = (await this.linesOf(n)).find((l) => t.pieces.some((p) => norm(l.text).includes(p.slice(0, 40))))
      if (line) best = { page: n, line, rx: null, state: 'quote' }
    }
    if (token !== this.token) return
    if (best) {
      await this.goPage(best.page, { line: best.line, rx: best.rx, t })
      this.onStatus({ state: best.state, page: best.page })
    } else {
      await this.goPage(1)
      this.onStatus({ state: 'not-found' })
    }
  }

  private linesOf(n: number): Promise<Line[]> {
    if (!this.lines.has(n)) this.lines.set(n, this.pdf!.getPage(n).then(pageLines))
    return this.lines.get(n)!
  }

  async goPage(n: number, focus: { line: Line; rx: RegExp | null; t: Targets } | null = null) {
    const pdf = this.pdf
    if (!pdf || n < 1 || n > pdf.numPages) return
    this.page = n
    const holder = this.host.querySelector('.sp-pages') as HTMLElement
    const page = await pdf.getPage(n)
    const width = Math.max(320, holder.clientWidth - 24)
    const viewport = page.getViewport({ scale: width / page.getViewport({ scale: 1 }).width })
    const ratio = window.devicePixelRatio || 1
    const wrap = document.createElement('div')
    wrap.className = 'sp-page'
    wrap.style.width = `${viewport.width}px`
    wrap.style.height = `${viewport.height}px`
    const canvas = document.createElement('canvas')
    canvas.width = Math.floor(viewport.width * ratio)
    canvas.height = Math.floor(viewport.height * ratio)
    canvas.style.width = `${viewport.width}px`
    canvas.style.height = `${viewport.height}px`
    wrap.append(canvas)
    holder.replaceChildren(wrap)
    ;(this.host.querySelector('.sp-pageno') as HTMLElement).textContent = `${n} of ${pdf.numPages}`
    await page.render({
      canvasContext: canvas.getContext('2d')!,
      viewport,
      transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined,
    }).promise

    const box = (item: { transform: number[]; width: number; height: number }, cls: string) => {
      const [x, y] = [item.transform[4], item.transform[5]]
      const h = item.height || Math.hypot(item.transform[2], item.transform[3])
      const [x1, y1, x2, y2] = viewport.convertToViewportRectangle([x, y, x + item.width, y + h])
      const el = document.createElement('div')
      el.className = `sp-hl ${cls}`
      Object.assign(el.style, {
        left: `${Math.min(x1, x2) - 2}px`, top: `${Math.min(y1, y2) - 2}px`,
        width: `${Math.abs(x2 - x1) + 4}px`, height: `${Math.abs(y2 - y1) + 4}px`,
      })
      wrap.append(el)
      return el
    }
    if (focus) {
      const { line, rx, t } = focus
      let first: HTMLElement | null = null
      for (const item of line.items) {
        if (rx) rx.lastIndex = 0
        if (rx && rx.test(item.str)) first = first || box(item, 'fig')
        else if (t.hasName(norm(item.str))) box(item, 'label')
      }
      const xs = line.items.map((i) => i.transform[4])
      const right = Math.max(...line.items.map((i) => i.transform[4] + i.width))
      const h = Math.max(...line.items.map((i) => i.height || 8))
      box({ transform: [1, 0, 0, 1, Math.min(...xs), line.items[0].transform[5]], width: right - Math.min(...xs), height: h }, 'row')
      centerIn(first || wrap)
    }
    if (this.findQuery) this.highlightFindOnPage(wrap, viewport)
  }

  /** Search inside the document; returns which match is showing. */
  async find(query: string, step = 1): Promise<{ count: number; index?: number; unit?: string }> {
    query = query.trim()
    if (!query) return { count: 0 }
    if (this.mode === 'html' && this.frame?.contentDocument) {
      const d = this.frame.contentDocument
      if (query !== this.findQuery) {
        d.querySelectorAll('mark.wb-find').forEach((m) => m.replaceWith(m.textContent || ''))
        this.findMarks = markText(d.body, new RegExp(escapeRe(query), 'gi'), 'wb-find', 500)
        this.findIndex = -1
        this.findQuery = query
      }
      if (!this.findMarks.length) return { count: 0 }
      this.findMarks[this.findIndex]?.classList.remove('wb-current')
      this.findIndex = (this.findIndex + step + this.findMarks.length) % this.findMarks.length
      const m = this.findMarks[this.findIndex]
      m.classList.add('wb-current')
      centerIn(m)
      return { count: this.findMarks.length, index: this.findIndex + 1 }
    }
    if (this.mode === 'pdf' && this.pdf) {
      if (query !== this.findQuery) {
        this.findQuery = query
        this.findHits = []
        for (let n = 1; n <= this.pdf.numPages; n++) {
          if ((await this.linesOf(n)).some((l) => norm(l.text).includes(norm(query)))) this.findHits.push(n)
        }
        this.findIndex = -1
      }
      if (!this.findHits.length) return { count: 0 }
      this.findIndex = (this.findIndex + step + this.findHits.length) % this.findHits.length
      await this.goPage(this.findHits[this.findIndex])
      return { count: this.findHits.length, index: this.findIndex + 1, unit: 'pages' }
    }
    return { count: 0 }
  }

  private async highlightFindOnPage(wrap: HTMLElement, viewport: PageViewport) {
    const q = norm(this.findQuery)
    for (const line of await this.linesOf(this.page)) {
      for (const item of line.items) {
        if (!norm(item.str).includes(q)) continue
        const [x, y] = [item.transform[4], item.transform[5]]
        const [x1, y1, x2, y2] = viewport.convertToViewportRectangle([x, y, x + item.width, y + (item.height || 8)])
        const el = document.createElement('div')
        el.className = 'sp-hl find'
        Object.assign(el.style, {
          left: `${Math.min(x1, x2)}px`, top: `${Math.min(y1, y2)}px`,
          width: `${Math.abs(x2 - x1)}px`, height: `${Math.abs(y2 - y1)}px`,
        })
        wrap.append(el)
      }
    }
  }
}
