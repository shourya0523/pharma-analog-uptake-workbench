import { describe, expect, it } from 'vitest'
import {
  buildChartData,
  calculateFilteredKpis,
  plottedNames,
  selectChartSeries,
} from './dashboardModel'

/**
 * The chart used to write `byPeriod[period][product] = point.value` for every
 * point in turn, so a quarter reported at three scopes plotted whichever row
 * the payload happened to end with - a worldwide quarter shown as its ex-U.S.
 * slice, with a tooltip that drilled to the slice while the quote beside it
 * led with the whole. These fix the two ways one period can carry more than
 * one published figure, and they are different defects: scopes that are all
 * correct and should not share a line, and same-scope readings that disagree
 * and have no right answer without reconciliation.
 *
 * Both answers are represented: a period with one reading still plots, and a
 * product with one scope still draws every point it has.
 */

const CALDERON = 'Calderon'
const NUVESSA = 'NuVessa'

function point(over: Record<string, unknown> = {}) {
  return {
    product: CALDERON,
    period: '2024Q4',
    period_type: 'quarterly',
    value: 1,
    validation_status: 'auto_pass',
    revenue_scope: 'Worldwide',
    scope_rank: 0,
    geography: null,
    formulation: null,
    reported_as: null,
    source_url: 'https://sec.gov/a-filing',
    source_quote: 'Calderon net sales',
    citation: {},
    ...over,
  }
}

const products = [{ product_name: CALDERON }, { product_name: NUVESSA }]

describe('one figure per product and period', () => {
  it('draws the widest scope a quarter reports, not the last row read', () => {
    const payload = {
      series: [
        point({ revenue_scope: 'Worldwide', scope_rank: 0, value: 144.1 }),
        point({ revenue_scope: 'U.S.', scope_rank: 1, geography: 'US', value: 124.1 }),
        point({ revenue_scope: 'ex-U.S.', scope_rank: 2, geography: 'ex-US', value: 20.0 }),
      ],
    }
    const selection = selectChartSeries(payload, products, 'quarterly')
    const row = selection.rows.find((item: any) => item.period === '2024Q4')
    expect(row[CALDERON]).toBe(144.1)
    expect(row[`__meta_${CALDERON}`].revenue_scope).toBe('Worldwide')
    expect(row[`__meta_${CALDERON}`].source_quote).toBe('Calderon net sales')
    expect(selection.scopes).toEqual([
      { product: CALDERON, scope: 'Worldwide', plotted: 1, suppressed: 2 },
    ])
  })

  it('never mixes two scopes into one line', () => {
    // The whole product in one quarter, only the U.S. slice in the next.
    // Joining them would draw a fall that no filing reports.
    const payload = {
      series: [
        point({ period: '2024Q3', revenue_scope: 'Worldwide', scope_rank: 0, value: 100 }),
        point({ period: '2024Q4', revenue_scope: 'U.S.', scope_rank: 1, value: 60 }),
      ],
    }
    const selection = selectChartSeries(payload, products, 'quarterly')
    expect(selection.rows.map((item: any) => item.period)).toEqual(['2024Q3'])
    expect(selection.rows[0][CALDERON]).toBe(100)
    // The quarter it does not draw is said out loud rather than dropped.
    expect(selection.scopes).toEqual([
      { product: CALDERON, scope: 'Worldwide', plotted: 1, suppressed: 1 },
    ])
  })

  it('leaves a single-scope product untouched', () => {
    const payload = {
      series: [
        point({ period: '2024Q3', value: 10 }),
        point({ period: '2024Q4', value: 12 }),
      ],
    }
    const selection = selectChartSeries(payload, products, 'quarterly')
    expect(selection.rows.map((item: any) => item[CALDERON])).toEqual([10, 12])
    expect(selection.scopes[0].suppressed).toBe(0)
    expect(selection.contested).toEqual([])
  })

  it('treats two readings of one figure as one figure', () => {
    const payload = {
      series: [point({ value: 127.2 }), point({ value: 127.2 }), point({ value: 127.2 })],
    }
    const selection = selectChartSeries(payload, products, 'quarterly')
    expect(selection.rows[0][CALDERON]).toBe(127.2)
    expect(selection.contested).toEqual([])
  })

  it('draws the reading reconciliation chose when two at one scope disagree', () => {
    const payload = {
      series: [
        point({ value: 139.9, citation: { corroborated_by: [{ datapoint_id: 'other' }] } }),
        point({ value: 138.5 }),
      ],
    }
    const selection = selectChartSeries(payload, products, 'quarterly')
    expect(selection.rows[0][CALDERON]).toBe(139.9)
    expect(selection.contested).toEqual([])
  })

  it('marks a period contested rather than averaging it or taking the last row', () => {
    const payload = {
      series: [point({ value: 139.9 }), point({ value: 138.5 })],
    }
    const selection = selectChartSeries(payload, products, 'quarterly')
    const row = selection.rows[0]
    expect(row[CALDERON]).toBeUndefined()
    expect(row[`__contested_${CALDERON}`].values).toEqual([138.5, 139.9])
    expect(selection.contested).toEqual([
      { product: CALDERON, period: '2024Q4', values: [138.5, 139.9] },
    ])
  })

  it('decides each product separately', () => {
    const payload = {
      series: [
        point({ value: 139.9 }),
        point({ value: 138.5 }),
        point({ product: NUVESSA, value: 7 }),
      ],
    }
    const selection = selectChartSeries(payload, products, 'quarterly')
    expect(selection.rows[0][NUVESSA]).toBe(7)
    expect(selection.contested.map((item) => item.product)).toEqual([CALDERON])
  })
})

describe('what counts as a quarter', () => {
  it('believes the row over its label', () => {
    // A nine-month figure and an undated one, both filed under a quarter's
    // label. Read off the label they join the quarterly curve as quarters,
    // and disagreeing with the quarter's own figure they take it down too.
    const payload = {
      series: [
        point({ value: 15.0 }),
        point({ period_type: 'nine_month', value: 48.0 }),
        point({ period_type: 'unknown', value: 25.0 }),
      ],
    }
    const selection = selectChartSeries(payload, products, 'quarterly')
    expect(selection.rows[0][CALDERON]).toBe(15.0)
    expect(selection.contested).toEqual([])
  })

  it('falls back to the label only where a row has no period type at all', () => {
    const payload = { series: [{ product: CALDERON, period: '2024Q4', value: 15.0 }] }
    expect(buildChartData(payload, products, 'quarterly')[0][CALDERON]).toBe(15.0)
  })

  it('keeps the annual tab to annual rows', () => {
    const payload = {
      series: [point({ period: '2024', period_type: 'annual', value: 46.0 }), point({ value: 15.0 })],
    }
    const rows = buildChartData(payload, products, 'annual')
    expect(rows.map((item: any) => item.period)).toEqual(['2024'])
  })
})

describe('what the chart can draw', () => {
  it('names only the products with a plotted line', () => {
    const payload = { series: [point({ product: NUVESSA, value: 7 })] }
    const rows = buildChartData(payload, products, 'quarterly')
    expect(plottedNames(rows)).toEqual([NUVESSA])
  })

  it('returns no rows for a tab whose series is empty', () => {
    const payload = { series: [point()], launch_series: [] }
    expect(buildChartData(payload, products, 'launch')).toEqual([])
    expect(plottedNames(buildChartData(payload, products, 'launch'))).toEqual([])
  })

  it('leaves the launch series alone, which carries one row per month already', () => {
    const payload = {
      launch_series: [
        { product: CALDERON, months_since_launch: 12, value: 0.4 },
        { product: CALDERON, months_since_launch: 36, value: 0.8 },
      ],
    }
    expect(buildChartData(payload, products, 'launch24').map((item: any) => item.period)).toEqual([
      'M12',
    ])
  })
})

describe('the aggregate peak tile', () => {
  it('is null when nothing has a selected peak, so the page can say so', () => {
    expect(calculateFilteredKpis([{ product_name: CALDERON }]).aggregatePeak).toBeNull()
  })

  it('is the sum when something does', () => {
    const kpis = calculateFilteredKpis([
      { product_name: CALDERON, selected_peak: { value: 750 } },
      { product_name: NUVESSA },
    ])
    expect(kpis.aggregatePeak).toBe(750)
    expect(kpis.peakCoverage).toBe('1/2')
  })
})
