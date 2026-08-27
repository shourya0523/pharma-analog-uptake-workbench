import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import DashboardPage from './DashboardPage'
import { buildChartData, calculateFilteredKpis, filterProducts } from './dashboardModel'

vi.mock('../api/client', () => ({
  api: { dashboard: vi.fn() },
}))

const products = [
  {
    job_id: 'one',
    product_name: 'Alpha',
    therapeutic_area: 'Oncology',
    company: 'Acme',
    approval_period: '2020-2024',
    competitive_intensity: 'high',
    roa: 'oral',
    moa: 'Kinase inhibitor',
    peak_sales_bucket: '$500M-$1B',
    indication_count: 1,
    approved_indications: 'Disease A',
    approved_lot: '2L+',
    selected_peak: { value: 750, type: 'consensus' },
    estimated_peak_revenue: 750,
    peak_type: 'consensus',
    uptake_ready: true,
    source_link: 'https://example.test/alpha',
    completeness_score: 90,
    validation_status: 'ready_for_review',
  },
  {
    job_id: 'two',
    product_name: 'Beta',
    therapeutic_area: 'Immunology',
    company: 'BetaCo',
    approval_period: '2015-2019',
    competitive_intensity: 'low',
    roa: 'inhaled',
    moa: null,
    peak_sales_bucket: '<$500M',
    indication_count: 2,
    approved_indications: 'Disease B',
    approved_lot: 'all_lines_or_unspecified',
    selected_peak: { value: 300, type: 'modeled' },
    estimated_peak_revenue: 300,
    peak_type: 'modeled',
    uptake_ready: false,
    completeness_score: 70,
    validation_status: 'ready_for_review',
  },
]

const payload = {
  products,
  series: [
    { product: 'Alpha', period: '2024Q1', period_type: 'quarterly', value: 10 },
    { product: 'Beta', period: '2024Q1', period_type: 'quarterly', value: 5 },
  ],
  launch_series: [
    {
      product: 'Alpha',
      period: '2025Q1',
      months_since_launch: 12,
      value: 0.4,
      source_url: 'https://example.test/alpha',
      source_quote: 'Alpha sales',
    },
    { product: 'Alpha', period: '2027Q1', months_since_launch: 36, value: 0.8 },
  ],
  filter_options: {},
  analog_count: 2,
}

function renderDashboard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/dashboard/run']}>
        <Routes>
          <Route path="/dashboard/:runId" element={<DashboardPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('dashboard production model', () => {
  it('intersects filters and recalculates KPIs', () => {
    const filtered = filterProducts(products, { company: 'Acme', moa: 'Kinase inhibitor' })
    expect(filtered.map((product) => product.product_name)).toEqual(['Alpha'])
    expect(calculateFilteredKpis(filtered)).toMatchObject({
      productsTracked: 1,
      aggregatePeak: 750,
      peakCoverage: '1/1',
    })
  })

  it('clamps the launch series to 24 months', () => {
    expect(buildChartData(payload, [products[0]], 'launch24').map((point) => point.period)).toEqual(['M12'])
  })
})

describe('DashboardPage', () => {
  beforeEach(() => {
    vi.mocked(api.dashboard).mockResolvedValue(payload)
  })

  it('updates table and KPIs when filters change and opens source details', async () => {
    const user = userEvent.setup()
    renderDashboard()
    await screen.findByRole('cell', { name: 'Alpha' })

    await user.selectOptions(screen.getByLabelText('Company'), 'Acme')
    expect(screen.queryByText('Beta')).not.toBeInTheDocument()
    const kpis = screen.getByLabelText('Filtered cohort KPIs')
    expect(within(kpis).getByText('$750M')).toBeInTheDocument()
    expect(within(kpis).getByText('Coverage 1/1')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'First 24 months' }))
    expect(screen.getByRole('button', { name: 'First 24 months' })).toHaveClass('active')

    await user.click(screen.getByRole('button', { name: 'Source' }))
    expect(screen.getByText('Source drill-through')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '×' }))
    expect(screen.queryByText('Source drill-through')).not.toBeInTheDocument()
  })
})

