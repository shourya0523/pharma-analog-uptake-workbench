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
    aggregatePeak: peaks.reduce((sum, product) => sum + Number(product.selected_peak.value || 0), 0),
    peakCoverage: `${peaks.length}/${products.length}`,
    uptakeReady: products.filter((product) => product.uptake_ready).length,
  }
}

export function buildChartData(payload: any, products: any[], tab: string): any[] {
  const selected = new Set(products.map((product) => product.product_name))
  let series =
    tab === 'launch' || tab === 'launch24'
      ? (payload?.launch_series || []).filter((point: any) => selected.has(point.product))
      : (payload?.series || []).filter((point: any) => selected.has(point.product))

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

  const byPeriod: Record<string, any> = {}
  for (const point of series) {
    const period =
      tab === 'launch' || tab === 'launch24'
        ? `M${point.months_since_launch}`
        : point.period
    byPeriod[period] ||= { period }
    byPeriod[period][point.product] = point.value
    byPeriod[period][`__meta_${point.product}`] = point
  }
  return Object.values(byPeriod).sort((a: any, b: any) => {
    if (/^M\d+$/.test(a.period) && /^M\d+$/.test(b.period)) {
      return Number(a.period.slice(1)) - Number(b.period.slice(1))
    }
    return String(a.period).localeCompare(String(b.period))
  })
}
