import { describe, expect, it } from 'vitest'
import { figureVariants, locateInHtml, locateQuoteInHtml, reveal, spacedText, targetsFor } from './sourcePreview'

const doc = (body: string) => new DOMParser().parseFromString(`<html><body>${body}</body></html>`, 'text/html')
const row = (over: Record<string, unknown> = {}) => ({
  datapoint_id: 'dp-1',
  product: 'Calderon',
  source_url: 'https://www.sec.gov/Archives/acme-ex99.htm',
  source_quote: 'Calderon | 240 | 8 | 248',
  value_reported: 248,
  ...over,
})

describe('figureVariants', () => {
  it('spells a number the ways documents print it', () => {
    expect(figureVariants(1234.5)).toEqual(expect.arrayContaining(['1234.5', '1,234.5', '1 234.5', '1.234,5']))
    expect(figureVariants(248)).toContain('248')
    expect(figureVariants(null)).toEqual([])
  })
})

describe('locateInHtml', () => {
  it('finds a figure in a row that puts every number in its own cell', () => {
    // No whitespace between cells: textContent reads "Calderon2408248".
    const d = doc('<table><tr><td>Calderon*</td><td>240</td><td>8</td><td>248</td></tr></table>')
    const tr = d.querySelector('tr')!
    expect(tr.textContent).toBe('Calderon*2408248')
    expect(spacedText(tr)).toBe('Calderon* 240 8 248')
    const hit = locateInHtml(d, targetsFor(row()))
    expect(hit?.el).toBe(tr)
    expect(hit?.exact).toBe(true)
  })

  it('does not take a longer number that contains the figure', () => {
    const d = doc('<table><tr><td>Calderon</td><td>1,248</td></tr></table>')
    expect(locateInHtml(d, targetsFor(row({ source_quote: '' })))).toBeNull()
  })

  it('finds a total printed on an unlabelled line beneath regional lines', () => {
    const d = doc(`<table>
      <tr><td>Calderon - U.S.</td><td>200</td></tr>
      <tr><td>Calderon - Europe</td><td>40</td></tr>
      <tr><td></td><td>248</td></tr>
      <tr><td>NuVessa - U.S.</td><td>248</td></tr></table>`)
    const hit = locateInHtml(d, targetsFor(row({ source_quote: '' })))
    expect(hit?.beneath?.textContent).toContain('Calderon - U.S.')
    expect(hit?.el.textContent).toBe('248')
  })

  it('prefers the named line over a total beneath it', () => {
    const d = doc('<table><tr><td>Calderon XR</td><td>248</td></tr><tr><td></td><td>248</td></tr></table>')
    const hit = locateInHtml(d, targetsFor(row({ product: 'Calderon XR', source_quote: '' })))
    expect(hit?.beneath).toBeUndefined()
    expect(hit?.el.textContent).toContain('Calderon XR')
  })
})

describe('locateQuoteInHtml', () => {
  it('falls back to the smallest element holding a verbatim piece of the quote', () => {
    const d = doc('<div><p>Other text.</p><p>Net product sales of Calderon were strong in the quarter.</p></div>')
    const hit = locateQuoteInHtml(d, targetsFor(row({ source_quote: 'net product sales of Calderon were strong', value_reported: null })))
    expect(hit?.tagName).toBe('P')
    expect(hit?.textContent).toContain('Net product sales')
  })
})

describe('reveal', () => {
  it('opens a section the page collapsed, so a figure inside it can be seen', () => {
    document.body.innerHTML = `<div class="read-more" style="display: none"><details><p hidden>
      <span id="fig">Calderon sales were 248</span></p></details></div>`
    const fig = document.getElementById('fig')!
    reveal(fig)
    for (let n: HTMLElement | null = fig; n && n !== document.body; n = n.parentElement) {
      expect(getComputedStyle(n).display).not.toBe('none')
      expect(n.hasAttribute('hidden')).toBe(false)
    }
    expect(document.querySelector('details')!.open).toBe(true)
  })
})
