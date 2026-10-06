import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type ReviewItem } from '../api/client'
import ReviewFocus from './ReviewFocus'

vi.mock('../api/client', () => ({
  api: { validationAction: vi.fn(), patchDatapoint: vi.fn() },
}))
// The document pane needs a browser's iframes and pdf.js; here it only has to
// say which figure it was asked to show.
vi.mock('./SourcePane', () => ({
  default: ({ row }: { row: { datapoint_id: string } }) => <div data-testid="pane">{row.datapoint_id}</div>,
}))

const item = (n: number, over: Partial<ReviewItem> = {}): ReviewItem => ({
  id: `vt-${n}`, type: 'flagged', product_id: 'prod-1', product: 'Calderon', job_id: 'job-1',
  period: `2024Q${n}`, reason: 'conflict', confidence: 0.8, datapoint_id: `dp-${n}`,
  value_normalized_usd_millions: 100 + n, revenue_scope: 'U.S.', source_url: 'https://example.test/q.htm',
  source_quote: `Calderon | ${100 + n}`, extraction_method: 'table', ...over,
})

function setup(items: ReviewItem[], onClose = vi.fn(), onResolved = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ReviewFocus items={items} start={0} help={{ conflict: 'Two readings disagree.' }} onClose={onClose} onResolved={onResolved} />
    </QueryClientProvider>,
  )
  return { onClose, onResolved }
}

describe('ReviewFocus', () => {
  afterEach(cleanup)
  beforeEach(() => {
    vi.mocked(api.validationAction).mockReset().mockResolvedValue({})
    vi.mocked(api.patchDatapoint).mockReset().mockResolvedValue({})
  })

  it('shows the figure beside its own document', () => {
    setup([item(1), item(2)])
    expect(screen.getByText('$101M')).toBeInTheDocument()
    expect(screen.getByTestId('pane')).toHaveTextContent('dp-1')
    expect(screen.getByText('Two readings disagree.')).toBeInTheDocument()
  })

  it('confirms with 1 and moves to the next figure and its document', async () => {
    const { onResolved } = setup([item(1), item(2)])
    await userEvent.keyboard('1')
    await waitFor(() => expect(api.validationAction).toHaveBeenCalledWith('vt-1', { action: 'confirm' }))
    await waitFor(() => expect(screen.getByTestId('pane')).toHaveTextContent('dp-2'))
    expect(onResolved).toHaveBeenCalled()
    expect(screen.getByText(/1 done/)).toBeInTheDocument()
  })

  it('rejects with R, follows up with U, steps with J and K, leaves with Esc', async () => {
    const { onClose } = setup([item(1), item(2), item(3)])
    await userEvent.keyboard('r')
    await waitFor(() => expect(api.validationAction).toHaveBeenCalledWith('vt-1', { action: 'reject' }))
    await waitFor(() => expect(screen.getByTestId('pane')).toHaveTextContent('dp-2'))
    await userEvent.keyboard('u')
    await waitFor(() => expect(api.validationAction).toHaveBeenCalledWith('vt-2', { action: 'follow_up' }))
    await userEvent.keyboard('k')
    await waitFor(() => expect(screen.getByTestId('pane')).toHaveTextContent('dp-2'))
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('edits with E and saves the corrected figure as confirmed', async () => {
    setup([item(1)])
    await userEvent.keyboard('e')
    const input = screen.getByLabelText(/Value, USD millions/)
    await userEvent.clear(input)
    await userEvent.type(input, '99.5')
    await userEvent.click(screen.getByText('Save as confirmed'))
    await waitFor(() =>
      expect(api.patchDatapoint).toHaveBeenCalledWith('dp-1', expect.objectContaining({
        value_normalized_usd_millions: 99.5, validation_status: 'confirmed',
      })),
    )
  })

  it('does not act twice on a figure already decided', async () => {
    setup([item(1)])
    await userEvent.keyboard('1')
    await waitFor(() => expect(api.validationAction).toHaveBeenCalledTimes(1))
    await userEvent.keyboard('1')
    await userEvent.keyboard('r')
    expect(api.validationAction).toHaveBeenCalledTimes(1)
  })
})
