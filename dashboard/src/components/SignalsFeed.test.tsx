import { fireEvent, render, screen, within } from '@testing-library/react'

import { SIGNALS_FIXTURE } from '../testing/fixtures'
import { SignalsFeed } from './SignalsFeed'

function renderFeed() {
  const onSelect = vi.fn()
  const onDateChange = vi.fn()
  render(
    <SignalsFeed
      signals={SIGNALS_FIXTURE}
      error={null}
      date=""
      today="2026-10-12"
      onDateChange={onDateChange}
      selectedSymbol={null}
      onSelect={onSelect}
    />,
  )
  return { onSelect, onDateChange }
}

describe('SignalsFeed', () => {
  it('shows each decision with its outcome badge and take/skip reason', () => {
    renderFeed()

    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(4)
    expect(within(items[0]).getByText('Risk blocked')).toBeTruthy()
    expect(within(items[0]).getByText('Risk blocked: max_open_positions')).toBeTruthy()
    expect(within(items[0]).getByText('MOMO')).toBeTruthy()
    expect(within(items[1]).getByText('Claude abstained')).toBeTruthy()
    expect(within(items[2]).getByText('Rules')).toBeTruthy()
    expect(within(items[3]).getByText('Would buy')).toBeTruthy()
    expect(within(items[3]).getByText('Would buy: 5266 @ 19.11')).toBeTruthy()
    expect(within(items[3]).getByText('stop 18.94 · target 19.45')).toBeTruthy()
    expect(within(items[3]).getByText('ORB')).toBeTruthy()
    expect(within(items[3]).getByText('10:00:20')).toBeTruthy()
  })

  it('selects the clicked signal symbol for the chart', () => {
    const { onSelect } = renderFeed()
    fireEvent.click(screen.getByText('Would buy: 5266 @ 19.11'))
    expect(onSelect).toHaveBeenCalledWith({ symbol: 'MMED', exchange: 'NASDAQ' })
  })

  it('can hide rule rejections and switch the session date', () => {
    const { onDateChange } = renderFeed()

    fireEvent.click(screen.getByRole('checkbox', { name: /Hide rule rejections \(1\)/ }))
    expect(screen.queryByText('Rules: no qualifying setup (price/liquidity/no breakout)')).toBeNull()
    expect(screen.getAllByRole('listitem')).toHaveLength(3)

    fireEvent.change(screen.getByLabelText('Signals date'), { target: { value: '2026-10-09' } })
    expect(onDateChange).toHaveBeenCalledWith('2026-10-09')
  })
})
