export const FILTER_KEYS = [
  'product_name',
  'therapeutic_area',
  'company',
  'approval_period',
  'competitive_intensity',
  'roa',
  'moa',
  'peak_sales_bucket',
  'indication_count',
] as const

export type FilterKey = (typeof FILTER_KEYS)[number]
export type DashboardFilters = Partial<Record<FilterKey, string>>

export function filterProducts(products: any[], filters: DashboardFilters): any[] {
  return products.filter((product) =>
    FILTER_KEYS.every((key) => {
      const selected = filters[key]
      return !selected || String(product[key] ?? '').toLowerCase() === selected.toLowerCase()
    }),
  )
}

export function uniqueOptions(
  products: any[],
  key: FilterKey,
  filters: DashboardFilters,
  apiOptions?: Record<string, string[]>,
): string[] {
  const scoped = filterProducts(products, { ...filters, [key]: undefined })
  const options = Array.from(
    new Map(
      scoped
        .map((product) => String(product[key] ?? '').trim())
        .filter(Boolean)
        .map((value) => [value.toLowerCase(), value]),
    ).values(),
  )
  return options.length ? options.sort((a, b) => a.localeCompare(b)) : [...(apiOptions?.[key] || [])]
}

export function calculateFilteredKpis(products: any[]) {
  const peaks = products.filter((product) => product.selected_peak)
  return {
    productsTracked: products.length,
    companiesRepresented: new Set(products.map((product) => product.company).filter(Boolean)).size,
    // null, not 0, where nothing has a selected peak: a sum over no products
    // is arithmetically zero and reads on screen as a measured zero.
    aggregatePeak: peaks.length
      ? peaks.reduce((sum, product) => sum + Number(product.selected_peak.value || 0), 0)
      : null,
    peakCoverage: `${peaks.length}/${products.length}`,
    uptakeReady: products.filter((product) => product.uptake_ready).length,
  }
}

/** One line's chosen scope, and how many of its product's points it leaves out. */
export type LineScope = {
  product: string
  scope: string
  plotted: number
  suppressed: number
}

/** A period whose figure two equally strong readings disagree about. */
export type ContestedPoint = {
  product: string
  period: string
  values: number[]
}

export type ChartSelection = {
  rows: any[]
  scopes: LineScope[]
  contested: ContestedPoint[]
}

const LAUNCH_TABS = new Set(['launch', 'launch24'])

/** The rank the server gave a point, or the front of the order if it gave none. */
function rankOf(point: any): number {
  return Number.isFinite(Number(point?.scope_rank)) ? Number(point.scope_rank) : 0
}

/**
 * Whether reconciliation settled this reading as the figure for its period.
 *
 * The winner of a group is the row the corroborating readings were attached
 * to, so a citation carrying `corroborated_by` is one the pipeline chose.
 * Nothing marks a loser as published, so the absence of the mark on every
 * candidate means reconciliation never ran on them.
 */
function reconciliationChoseIt(point: any): boolean {
  const cited = point?.citation?.corroborated_by
  return Array.isArray(cited) && cited.length > 0
}

function groupBy<T>(items: T[], key: (item: T) => string): Map<string, T[]> {
  const grouped = new Map<string, T[]>()
  for (const item of items) {
    const at = key(item)
    const bucket = grouped.get(at)
    if (bucket) bucket.push(item)
    else grouped.set(at, [item])
  }
  return grouped
}

/**
 * One figure per (product, period), from one scope per product.
 *
 * A quarter can carry a worldwide figure, a U.S. figure and an ex-U.S. one,
 * all correct and all published; plotting them on one line means plotting
 * whichever was read last, which is how a line meant to show the whole
 * product showed a region of it. So each line draws the widest scope its
 * product has - `scope_rank`, which the server derives from the same
 * grouping reconciliation uses - and never mixes two.
 *
 * Within that scope, a period read twice is one figure if the readings agree.
 * Where they do not, the figure is the one reconciliation chose; where it
 * chose none, the period is contested and nothing is drawn for it, because
 * averaging two figures produces a third that no document contains.
 */
function selectOneFigurePerPeriod(points: any[]): {
  chosen: any[]
  scopes: LineScope[]
  contested: ContestedPoint[]
} {
  const chosen: any[] = []
  const scopes: LineScope[] = []
  const contested: ContestedPoint[] = []

  for (const [product, all] of groupBy(points, (point) => String(point.product))) {
    const best = Math.min(...all.map(rankOf))
    const kept = all.filter((point) => rankOf(point) === best)
    const labels = Array.from(
      new Set(kept.map((point) => String(point.revenue_scope ?? '')).filter(Boolean)),
    )
    for (const [period, candidates] of groupBy(kept, (point) => String(point.period))) {
      if (candidates.length === 1) {
        chosen.push(candidates[0])
        continue
      }
      const values = Array.from(new Set(candidates.map((point) => Number(point.value))))
      if (values.length === 1) {
        chosen.push(candidates[0])
        continue
      }
      const winners = candidates.filter(reconciliationChoseIt)
      if (winners.length === 1) {
        chosen.push(winners[0])
        continue
      }
      contested.push({ product, period, values: values.sort((a, b) => a - b) })
    }
    scopes.push({
      product,
      scope: labels.join(' / ') || 'unlabelled',
      plotted: kept.length,
      suppressed: all.length - kept.length,
    })
  }
  return { chosen, scopes, contested }
}

/**
 * The chart's rows, plus what had to be decided to produce them.
 *
 * The launch tabs read a different series, which carries one row per
 * product and month already, so nothing is selected there.
 */
export function selectChartSeries(payload: any, products: any[], tab: string): ChartSelection {
  const selected = new Set(products.map((product) => product.product_name))
  const launch = LAUNCH_TABS.has(tab)
  let series = (launch ? payload?.launch_series || [] : payload?.series || []).filter(
    (point: any) => selected.has(point.product),
  )

  if (tab === 'quarterly') {
    series = series.filter(
      (point: any) =>
        (point.period_type || '').toLowerCase() === 'quarterly' || /Q[1-4]/i.test(String(point.period)),
    )
  } else if (tab === 'annual') {
    series = series.filter(
      (point: any) =>
        (point.period_type || '').toLowerCase() === 'annual' ||
        (/^\d{4}$/.test(String(point.period)) && !/Q/i.test(String(point.period))),
    )
  } else if (tab === 'launch24') {
    series = series.filter((point: any) => point.months_since_launch != null && point.months_since_launch <= 24)
  }

  const picked = launch
    ? { chosen: series, scopes: [] as LineScope[], contested: [] as ContestedPoint[] }
    : selectOneFigurePerPeriod(series)

  const byPeriod: Record<string, any> = {}
  const at = (point: any) => (launch ? `M${point.months_since_launch}` : String(point.period))
  for (const point of picked.chosen) {
    const period = at(point)
    byPeriod[period] ||= { period }
    byPeriod[period][point.product] = point.value
    byPeriod[period][`__meta_${point.product}`] = point
  }
  // A contested period gets a row and no value, so the line breaks there
  // rather than closing over a figure nobody stands behind.
  for (const point of picked.contested) {
    byPeriod[point.period] ||= { period: point.period }
    byPeriod[point.period][`__contested_${point.product}`] = point
  }

  const rows = Object.values(byPeriod).sort((a: any, b: any) => {
    if (/^M\d+$/.test(a.period) && /^M\d+$/.test(b.period)) {
      return Number(a.period.slice(1)) - Number(b.period.slice(1))
    }
    return String(a.period).localeCompare(String(b.period))
  })
  return { rows, scopes: picked.scopes, contested: picked.contested }
}

export function buildChartData(payload: any, products: any[], tab: string): any[] {
  return selectChartSeries(payload, products, tab).rows
}

/** The products the chart can actually draw a line for, in the rows' order. */
export function plottedNames(rows: any[]): string[] {
  const names: string[] = []
  const seen = new Set<string>()
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (key === 'period' || key.startsWith('__')) continue
      if (!seen.has(key)) {
        seen.add(key)
        names.push(key)
      }
    }
  }
  return names.sort((a, b) => a.localeCompare(b))
}
