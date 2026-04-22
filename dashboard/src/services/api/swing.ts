import type {
  SwingDashboard,
  BreadthMetrics,
  GuruScreenerResult,
  GuruScreenerName,
  ATRMatrixRow,
  Club97Entry,
  StockbeeMover,
  StockbeeScanName,
  IndustryGroup,
  StageDistribution,
  TrendGradeDistribution,
} from '@/types'
import { get } from './client'

export const fetchSwingDashboard = () =>
  get<SwingDashboard>('/api/swing/dashboard')

export const fetchSwingBreadth = () =>
  get<BreadthMetrics>('/api/swing/breadth')

export const fetchGuruScreener = (name: GuruScreenerName) =>
  get<GuruScreenerResult[]>(`/api/swing/screener/${name}`)

export const fetchATRMatrix = () =>
  get<ATRMatrixRow[]>('/api/swing/atr-matrix')

export const fetchClub97 = () =>
  get<Club97Entry[]>('/api/swing/club97')

export const fetchStockbeeScan = (scan: StockbeeScanName) =>
  get<StockbeeMover[]>(`/api/swing/stockbee/${scan}`)

export const fetchIndustries = () =>
  get<IndustryGroup[]>('/api/swing/industries')

export const fetchStages = () =>
  get<StageDistribution>('/api/swing/stages')

export const fetchGrades = () =>
  get<TrendGradeDistribution>('/api/swing/grades')
