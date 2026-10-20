/**
 * Answers the API from static files, for a build with no backend behind it.
 *
 * Enabled by VITE_DEMO=1. The files under demo-data/demo/ are written by
 * scripts/build_demo_data.py in the live API's response shapes, so pages read
 * them unchanged, and only a demo build copies them into dist/demo/.
 * Every write is refused: the demo is read-only.
 */

export const DEMO = import.meta.env.VITE_DEMO === '1'

export const DEMO_READ_ONLY =
  'This is a read-only demo built from the gold dataset; nothing can be changed or run here.'

const cache = new Map<string, Promise<any>>()

function file<T>(name: string): Promise<T> {
  if (!cache.has(name)) {
    const url = `${import.meta.env.BASE_URL}demo/${name}`
    cache.set(
      name,
      fetch(url).then((res) => {
        if (!res.ok) {
          cache.delete(name)
          throw new Error(res.status === 404 ? 'Not in the demo dataset.' : res.statusText)
        }
        return res.json()
      }),
    )
  }
  return cache.get(name)!
}

const EMPTY_QUEUE = {
  groups: [],
  groups_total: 0,
  limit: 0,
  offset: 0,
  total: 0,
  flagged: 0,
  missing: 0,
  reasons: {},
  products: [],
  reason_help: {},
}

export async function demoRequest<T>(path: string, init?: RequestInit): Promise<T> {
  if (init?.method && init.method !== 'GET') throw new Error(DEMO_READ_ONLY)
  const url = new URL(path, 'http://demo.local')
  const route = url.pathname

  if (route === '/products') {
    const all = await file<{ products: any[]; total: number }>('products.json')
    const q = (url.searchParams.get('q') || '').toLowerCase()
    const cadence = url.searchParams.get('cadence')
    const products = all.products.filter(
      (p) =>
        (!q || `${p.name} ${p.generic || ''}`.toLowerCase().includes(q)) &&
        (!cadence || p.cadence === cadence),
    )
    return { products, total: products.length } as T
  }
  const product = route.match(/^\/products\/([^/]+)$/)
  if (product) return file<T>(`products/${encodeURIComponent(product[1])}.json`)
  if (route === '/dashboard/preview') return file<T>('dashboard.json')
  if (route === '/review/queue') return EMPTY_QUEUE as T
  if (route === '/observability') {
    return {
      health: { status: 'ok', environment: 'demo' },
      available_tables: [],
      tables: [],
      job_status_counts: {},
      recent_runs: [],
      recent_job_errors: [],
      log_buffer_size: 0,
    } as T
  }
  if (route === '/observability/logs') return { count: 0, logs: [] } as T
  if (route.startsWith('/observability/db/')) return { columns: [], rows: [], total: 0 } as T
  throw new Error(DEMO_READ_ONLY)
}

/** What the demo holds, for the banner. */
export function demoAbout(): Promise<{
  products: number
  quarterly_rows: number
  annual_rows: number
  excluded_products: number
  uptake_products: number
}> {
  return file('about.json')
}
