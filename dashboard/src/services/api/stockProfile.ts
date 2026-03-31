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
  get<StockOverview>(`/api/stock/${symbol}/overview`)

export const fetchStockKeyStats = (symbol: string) =>
  get<StockKeyStats>(`/api/stock/${symbol}/key-stats`)

export const fetchStockFinancials = (symbol: string) =>
  get<StockFinancials>(`/api/stock/${symbol}/financials`)

export const fetchStockAnalyst = (symbol: string) =>
  get<StockAnalyst>(`/api/stock/${symbol}/analyst`)

export const fetchStockOwnership = (symbol: string) =>
  get<StockOwnership>(`/api/stock/${symbol}/ownership`)

export const fetchStockEvents = (symbol: string) =>
  get<StockEvents>(`/api/stock/${symbol}/events`)

export const fetchStockNarrative = (symbol: string) =>
  get<StockNarrative>(`/api/stock/${symbol}/narrative`)

export const fetchStockFinancialStatements = (symbol: string) =>
  get<StockFinancialStatements>(`/api/stock/${symbol}/financial-statements`)

export const fetchStockAnalystDetail = (symbol: string) =>
  get<StockAnalystDetail>(`/api/stock/${symbol}/analyst-detail`)

export const fetchStockRatingScorecard = (symbol: string) =>
  get<StockRatingScorecard>(`/api/stock/${symbol}/rating-scorecard`)

export const fetchStockCompanyInfo = (symbol: string) =>
  get<StockCompanyInfo>(`/api/stock/${symbol}/company-info`)

export const fetchStockSplits = (symbol: string) =>
  get<StockSplits>(`/api/stock/${symbol}/stock-splits`)

export const fetchStockEarningsDetail = (symbol: string) =>
  get<StockEarningsDetail>(`/api/stock/${symbol}/earnings-detail`)

export const fetchStockProfile = (symbol: string) =>
  get<StockProfileBundle>(`/api/stock/${symbol}/profile`)
