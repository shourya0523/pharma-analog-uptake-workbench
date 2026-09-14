import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import ReviewQueuePage from './ReviewQueuePage'

vi.mock('../api/client', () => ({
  api: { reviewQueue: vi.fn() },
}))

const contested = {
  product_id: 'prod-1',
  product: 'Calderon',
  job_id: 'job-1',
  period: '2024Q2',
  types: ['flagged'],
  reasons: ['conflict', 'recent_period'],
  items: [
    {
      id: 'vt-1', type: 'flagged', product_id: 'prod-1', product: 'Calderon', job_id: 'job-1',
      period: '2024Q2', reason: 'conflict', confidence: 0.8, datapoint_id: 'dp-1',
      value_normalized_usd_millions: 186.4, revenue_scope: 'U.S.',
    },
    {
      id: 'vt-2', type: 'flagged', product_id: 'prod-1', product: 'Calderon', job_id: 'job-1',
      period: '2024Q2', reason: 'recent_period', confidence: 0.97, datapoint_id: 'dp-2',
      value_normalized_usd_millions: 190.1, revenue_scope: 'U.S.',
    },
  ],
}
const gap = {
  product_id: 'prod-2',
  product: 'NuVessa',
  job_id: 'job-2',
  period: '2024Q1',
  types: ['missing'],
  reasons: ['interior_gap'],
  items: [
    {
      id: 'uq-1', type: 'missing', product_id: 'prod-2', product: 'NuVessa', job_id: 'job-2',
      period: '2024Q1', reason: 'interior_gap', confidence: 0.3,
      reason_unresolved: 'No reliable product-level quarterly value extracted',
    },
  ],
}

function page(groups: any[], offset: number) {
  return {
    groups,
    groups_total: 2,
    limit: 1,
    offset,
    total: 3,
    flagged: 2,
    missing: 1,
    reasons: { conflict: 1, interior_gap: 1, recent_period: 1 },
    products: [
      { id: 'prod-1', name: 'Calderon' },
      { id: 'prod-2', name: 'NuVessa' },
    ],
    reason_help: { conflict: 'Two candidates disagreed for this quarter.' },
  }
}

function renderQueue() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/review']}>
        <ReviewQueuePage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ReviewQueuePage', () => {
  beforeEach(() => {
    vi.mocked(api.reviewQueue).mockImplementation(async (params) =>
      params?.offset ? page([gap], params.offset) : page([contested], 0),
    )
  })

  it('says a contested quarter once, with its figures beneath, and pages by question', async () => {
    const user = userEvent.setup()
    renderQueue()
    await screen.findByText('2 questions to work')

    // One heading for the quarter, two figures under it.
    const head = screen.getByText('2 figures in question').closest('header') as HTMLElement
    expect(within(head).getByText('Calderon')).toBeInTheDocument()
    expect(screen.getAllByText('flagged value')).toHaveLength(2)

    // The reason's meaning comes from the response, not from the page.
    await user.click(screen.getByText('conflict', { selector: '.queue-item-head .pill.reason' }))
    expect(screen.getByText('Two candidates disagreed for this quarter.')).toBeInTheDocument()

    // The filter options describe the whole set, not the page shown.
    expect(screen.getByRole('option', { name: 'interior_gap (1)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'NuVessa' })).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: 'Next →' })[0])
    await screen.findByText('missing quarter')
    expect(api.reviewQueue).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 50, limit: 50 }),
    )
    expect(screen.queryByText('2 figures in question')).not.toBeInTheDocument()
  })
})
