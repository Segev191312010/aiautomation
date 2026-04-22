import type { AppRoute } from '@/types'

export const APP_ROUTE_PATHS: Record<AppRoute, string> = {
  dashboard: '/',
  tradebot: '/tradebot',
  market: '/market',
  charts: '/charts',
  rotation: '/rotation',
  screener: '/screener',
  swing: '/swing',
  simulation: '/simulation',
  backtest: '/backtest',
  rules: '/rules',
  alerts: '/alerts',
  settings: '/settings',
  stock: '/stock',
  analytics: '/analytics',
  advisor: '/advisor',
}

const ROUTE_ENTRIES = Object.entries(APP_ROUTE_PATHS) as Array<[AppRoute, string]>

function normalizePath(pathname: string): string {
  if (!pathname || pathname === '/') return '/'
  return pathname.replace(/\/+$/, '') || '/'
}

export function getRoutePath(route: AppRoute): string {
  return APP_ROUTE_PATHS[route]
}

export function getRouteFromPath(pathname: string): AppRoute {
  const normalized = normalizePath(pathname)
  const match = ROUTE_ENTRIES.find(([, path]) => path === normalized)
  return match?.[0] ?? 'dashboard'
}

export function navigateToPath(path: string): void {
  const normalizedTarget = normalizePath(path)
  const currentPath = normalizePath(window.location.pathname)
  if (normalizedTarget === currentPath) return
  window.history.pushState(null, '', normalizedTarget)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function navigateToRoute(route: AppRoute): void {
  navigateToPath(getRoutePath(route))
}
