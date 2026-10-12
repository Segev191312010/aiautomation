import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'

import App from './App'
import { installFetch, jsonResponse, liveApi, TOKEN_KEY } from './testing/fixtures'

function headerOf(call: unknown[] | undefined, name: string): string | null {
  const init = call?.[1] as RequestInit | undefined
  return new Headers(init?.headers).get(name)
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.sessionStorage.clear()
})

describe('live dashboard auth gate', () => {
  it('exchanges the pasted bootstrap secret for a tab-scoped token', async () => {
    const api = liveApi()
    const fetchMock = installFetch((path, init) =>
      path === '/api/auth/token' ? jsonResponse({ access_token: 'token-123', token_type: 'bearer' }) : api(path, init),
    )
    render(<App />)

    expect(screen.getByRole('heading', { name: 'Unlock the dashboard' })).toBeTruthy()
    expect(screen.getByText('JWT_BOOTSTRAP_SECRET')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Bootstrap secret'), { target: { value: '  s3cret-value ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Unlock' }))

    expect(await screen.findByRole('heading', { name: 'Live screener' })).toBeTruthy()
    const tokenCall = fetchMock.mock.calls[0]
    expect(tokenCall[0]).toBe('/api/auth/token')
    expect(tokenCall[1]?.method).toBe('POST')
    expect(headerOf(tokenCall, 'X-Bootstrap-Secret')).toBe('s3cret-value')
    expect(window.sessionStorage.getItem(TOKEN_KEY)).toBe('token-123')

    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => path === '/api/live/status')).toBe(true))
    const statusCall = fetchMock.mock.calls.find(([path]) => path === '/api/live/status')
    expect(headerOf(statusCall, 'Authorization')).toBe('Bearer token-123')
    // Only the issued token is kept; the secret itself never reaches storage.
    expect(Object.values({ ...window.sessionStorage }).join(' ')).not.toContain('s3cret-value')
  })

  it('explains a rejected secret inline', async () => {
    installFetch(() => jsonResponse({ detail: 'Invalid bootstrap secret' }, 401))
    render(<App />)
    fireEvent.change(screen.getByLabelText('Bootstrap secret'), { target: { value: 'wrong' } })
    fireEvent.click(screen.getByRole('button', { name: 'Unlock' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('That secret was rejected')
    expect(window.sessionStorage.getItem(TOKEN_KEY)).toBeNull()
  })

  it('returns to the gate when the API rejects the stored token', async () => {
    window.sessionStorage.setItem(TOKEN_KEY, 'expired-token')
    installFetch(() => jsonResponse({ detail: 'Invalid or expired token' }, 401))
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Unlock the dashboard' })).toBeTruthy()
    expect(screen.getByText(/session expired or was rejected/i)).toBeTruthy()
    expect(window.sessionStorage.getItem(TOKEN_KEY)).toBeNull()
  })
})

describe('live dashboard data', () => {
  beforeEach(() => {
    window.sessionStorage.setItem(TOKEN_KEY, 'token-abc')
  })

  it('renders the screener, signal reasons, orders, and header counts', async () => {
    installFetch(liveApi())
    render(<App />)

    expect(await screen.findByText('Would buy: 5266 @ 19.11')).toBeTruthy()
    const screener = screen.getByRole('region', { name: 'Live screener' })
    const symbols = within(screener)
      .getAllByRole('row')
      .slice(1)
      .map((row) => row.querySelector('.symbol')?.textContent)
    expect(symbols).toEqual(['NVDA', 'AMD', 'BRK B'])
    expect(screen.getByText('Risk blocked: max_open_positions')).toBeTruthy()
    expect(screen.getByText('Claude abstained: Extended into resistance.')).toBeTruthy()

    const orders = screen.getByRole('region', { name: 'PAPER orders' })
    expect(within(orders).getByText('BUY 5266 @ 19.11 · stop 18.94 · target 19.45 · #101')).toBeTruthy()
    expect(within(orders).getByText('Filled 5266/5266 @ 19.1100')).toBeTruthy()
    expect(within(orders).getByText('Open')).toBeTruthy()

    expect(screen.getByText('Market open')).toBeTruthy()
    const counts = screen.getByLabelText("Today's activity")
    expect(within(counts).getByText('Signals').nextSibling?.textContent).toBe('4')
    expect(within(counts).getByText('Would buy').nextSibling?.textContent).toBe('1')
    expect(within(counts).getByText('Orders sent').nextSibling?.textContent).toBe('1')
    expect(within(orders).getByText('Realized P&L today')).toBeTruthy()

    const regime = screen.getByLabelText('Market regime')
    expect(within(regime).getByText('Risk-on').className).toContain('regime-risk-on')
    expect(within(regime).getByText(/^S&P \+0\.5% · Nasdaq \+0\.8%/)).toBeTruthy()

    const manual = screen.getByRole('region', { name: 'Manual orders' })
    expect(within(manual).getByText('Rejected')).toBeTruthy()
    expect(within(manual).getByText('naked_short: no TSLA shares outside bot brackets')).toBeTruthy()
    expect(within(manual).getByText('Filled 1/1 @ 227.31')).toBeTruthy()
  })

  it('polls the market context and the manual orders with the same bearer token', async () => {
    const fetchMock = installFetch(liveApi())
    render(<App />)

    await screen.findByText('Would buy: 5266 @ 19.11')
    for (const path of ['/api/live/market', '/api/live/manual-orders']) {
      const call = fetchMock.mock.calls.find(([url]) => url === path)
      expect(headerOf(call, 'Authorization')).toBe('Bearer token-abc')
    }
  })

  it('treats an unrecognised regime as no market context and isolates a failing manual-order ledger', async () => {
    installFetch(
      liveApi({
        '/api/live/market': () => jsonResponse({ available: true, regime: 'euphoric', summary: 'To the moon' }),
        '/api/live/manual-orders': () => jsonResponse({ detail: 'Manual order ledger unreadable: no such column' }, 503),
      }),
    )
    render(<App />)

    expect(await screen.findByText('Manual orders: Manual order ledger unreadable: no such column')).toBeTruthy()
    expect(within(screen.getByLabelText('Market regime')).getByText('No regime')).toBeTruthy()
    expect(screen.queryByText('To the moon')).toBeNull()
    expect(screen.getByText('Would buy: 5266 @ 19.11')).toBeTruthy()
  })

  it('shows a failing endpoint inline while the rest keeps rendering', async () => {
    installFetch(
      liveApi({
        '/api/live/orders': () => jsonResponse({ detail: 'PAPER bridge state file is not valid JSON' }, 503),
      }),
    )
    render(<App />)

    expect(await screen.findByText('Orders: PAPER bridge state file is not valid JSON')).toBeTruthy()
    expect(screen.getByText('Would buy: 5266 @ 19.11')).toBeTruthy()
    expect(within(screen.getByRole('region', { name: 'Live screener' })).getByText('NVDA')).toBeTruthy()
  })

  it('charts the top-ranked symbol, then whatever signal the operator clicks', async () => {
    installFetch(liveApi())
    render(<App />)

    const host = await screen.findByTestId('tradingview-host')
    await waitFor(() => expect(host.dataset.symbol).toBe('NASDAQ:NVDA'))
    fireEvent.click(await screen.findByText('Would buy: 5266 @ 19.11'))
    await waitFor(() => expect(host.dataset.symbol).toBe('NASDAQ:MMED'))
    await waitFor(() => expect(host.querySelector('script')?.textContent).toContain('"symbol":"NASDAQ:MMED"'))
    expect(host.querySelectorAll('script')).toHaveLength(1)
  })
})
