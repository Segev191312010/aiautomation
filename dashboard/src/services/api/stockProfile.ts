import type {
  StockAnalyst,
  StockAnalystDetail,
  StockCompanyInfo,
  StockEarningsDetail,
  StockEvents,
  StockFinancials,
  StockFinancialStatements,
  StockKeyStats,
  StockNarrative,
  StockOverview,
  StockOwnership,
  StockProfileBundle,
  StockRatingScorecard,
  StockSplits,
} from '@/types'
import { get } from './client'

export const fetchStockOverview = (symbol: string) =>
  get<StockOverview>(`/api/stock/${encodeURIComponent(symbol)}/overview`)

export const fetchStockKeyStats = (symbol: string) =>
  get<StockKeyStats>(`/api/stock/${encodeURIComponent(symbol)}/key-stats`)

export const fetchStockFinancials = (symbol: string) =>
  get<StockFinancials>(`/api/stock/${encodeURIComponent(symbol)}/financials`)

export const fetchStockAnalyst = (symbol: string) =>
  get<StockAnalyst>(`/api/stock/${encodeURIComponent(symbol)}/analyst`)

export const fetchStockOwnership = (symbol: string) =>
  get<StockOwnership>(`/api/stock/${encodeURIComponent(symbol)}/ownership`)

export const fetchStockEvents = (symbol: string) =>
  get<StockEvents>(`/api/stock/${encodeURIComponent(symbol)}/events`)

export const fetchStockNarrative = (symbol: string) =>
  get<StockNarrative>(`/api/stock/${encodeURIComponent(symbol)}/narrative`)

export const fetchStockFinancialStatements = (symbol: string) =>
  get<StockFinancialStatements>(`/api/stock/${encodeURIComponent(symbol)}/financial-statements`)

export const fetchStockAnalystDetail = (symbol: string) =>
  get<StockAnalystDetail>(`/api/stock/${encodeURIComponent(symbol)}/analyst-detail`)

export const fetchStockRatingScorecard = (symbol: string) =>
  get<StockRatingScorecard>(`/api/stock/${encodeURIComponent(symbol)}/rating-scorecard`)

export const fetchStockCompanyInfo = (symbol: string) =>
  get<StockCompanyInfo>(`/api/stock/${encodeURIComponent(symbol)}/company-info`)

export const fetchStockSplits = (symbol: string) =>
  get<StockSplits>(`/api/stock/${encodeURIComponent(symbol)}/stock-splits`)

export const fetchStockEarningsDetail = (symbol: string) =>
  get<StockEarningsDetail>(`/api/stock/${encodeURIComponent(symbol)}/earnings-detail`)

export const fetchStockProfile = (symbol: string) =>
  get<StockProfileBundle>(`/api/stock/${encodeURIComponent(symbol)}/profile`)
