import { fireEvent, render, screen, within } from '@testing-library/react'

import { SCREENER_FIXTURE } from '../testing/fixtures'
import type { ScreenerSnapshot } from '../types'
import { ScreenerTable } from './ScreenerTable'

function renderTable(snapshot: ScreenerSnapshot | null = SCREENER_FIXTURE) {
  const onSelect = vi.fn()
  const view = render(<ScreenerTable snapshot={snapshot} error={null} selectedSymbol="NVDA" onSelect={onSelect} />)
  return { onSelect, ...view }
}

function visibleSymbols(): (string | null | undefined)[] {
  return screen
    .getAllByRole('row')
    .slice(1)
    .map((row) => row.querySelector('.symbol')?.textContent)
}

describe('ScreenerTable', () => {
  it('renders formatted rows ranked by score with hot ranks highlighted', () => {
    renderTable()

    expect(visibleSymbols()).toEqual(['NVDA', 'AMD', 'BRK B'])
    const nvda = screen.getAllByRole('row')[1]
    expect(within(nvda).getAllByRole('cell').map((cell) => cell.textContent)).toEqual([
      'NVDA#1',
      '123.45',
      '+2.88%',
      '+0.83%',
      '2.40×',
      '12.3M',
      '-0.44%',
      '0.871',
    ])
    expect(nvda.className).toContain('hot')
    expect(nvda.className).toContain('selected')
    expect(screen.getAllByRole('row')[3].className).not.toContain('hot')
    expect(screen.getByText(/503 watchlist · 410 qualified · 400 quoted · 10 errors · sweep 12.3 s/)).toBeTruthy()
  })

  it('sorts by change and RVOL, toggling direction on a second click', () => {
    renderTable()

    fireEvent.click(screen.getByRole('button', { name: /Chg %/ }))
    expect(visibleSymbols()).toEqual(['AMD', 'NVDA', 'BRK B'])
    fireEvent.click(screen.getByRole('button', { name: /Chg %/ }))
    expect(visibleSymbols()).toEqual(['BRK B', 'NVDA', 'AMD'])
    expect(screen.getByRole('columnheader', { name: /Chg %/ }).getAttribute('aria-sort')).toBe('ascending')

    fireEvent.click(screen.getByRole('button', { name: /RVOL/ }))
    expect(visibleSymbols()).toEqual(['AMD', 'NVDA', 'BRK B'])
    expect(screen.getByRole('columnheader', { name: /RVOL/ }).getAttribute('aria-sort')).toBe('descending')

    fireEvent.click(screen.getByRole('button', { name: /Score/ }))
    expect(visibleSymbols()).toEqual(['NVDA', 'AMD', 'BRK B'])
  })

  it('filters to hot rows or a symbol and reports the clicked selection', () => {
    const { onSelect } = renderTable()

    fireEvent.click(screen.getByRole('checkbox', { name: /Hot only \(2\)/ }))
    expect(visibleSymbols()).toEqual(['NVDA', 'AMD'])
    fireEvent.click(screen.getByText('AMD'))
    expect(onSelect).toHaveBeenCalledWith({ symbol: 'AMD', exchange: 'NASDAQ' })

    fireEvent.click(screen.getByRole('checkbox', { name: /Hot only/ }))
    fireEvent.change(screen.getByLabelText('Filter symbols'), { target: { value: 'brk' } })
    expect(visibleSymbols()).toEqual(['BRK B'])
    fireEvent.keyDown(screen.getAllByRole('row')[1], { key: 'Enter' })
    expect(onSelect).toHaveBeenLastCalledWith({ symbol: 'BRK B', exchange: 'NYSE' })
  })

  it('explains stale and empty snapshots instead of rendering nothing', () => {
    const { rerender } = renderTable({
      ...SCREENER_FIXTURE,
      stale: true,
      session_date: '2026-10-09',
      snapshot_age_seconds: 3600,
    })
    expect(screen.getByText(/No snapshot for today yet\. Showing the last one from 2026-10-09 \(1h 00m old\)/)).toBeTruthy()

    rerender(
      <ScreenerTable
        snapshot={{ schema: 'screener.v1', rows: [], empty: true, stale: false, snapshot_age_seconds: null }}
        error={null}
        selectedSymbol={null}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByText(/No screener snapshot yet/)).toBeTruthy()
  })
})
