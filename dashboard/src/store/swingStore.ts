import { create } from 'zustand'
import type {
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
  SwingDashboard,
} from '@/types'

interface SwingState {
  breadth:          BreadthMetrics | null
  guruResults:      Partial<Record<GuruScreenerName, GuruScreenerResult[]>>
  atrMatrix:        ATRMatrixRow[]
  club97:           Club97Entry[]
  stockbeeResults:  Partial<Record<StockbeeScanName, StockbeeMover[]>>
  industries:       IndustryGroup[]
  stages:           StageDistribution | null
  grades:           TrendGradeDistribution | null

  loading:          boolean
  sectionLoading:   Record<string, boolean>
  error:            string | null
  lastUpdate:       Date | null
  activeGuruTab:    GuruScreenerName
  activeStockbeeTab: StockbeeScanName

  setDashboard:      (data: SwingDashboard) => void
  setGuruTab:        (tab: GuruScreenerName) => void
  setStockbeeTab:    (tab: StockbeeScanName) => void
  setLoading:        (loading: boolean) => void
  setSectionLoading: (section: string, loading: boolean) => void
  setError:          (error: string | null) => void
}

export const useSwingStore = create<SwingState>((set) => ({
  breadth:          null,
  guruResults:      {},
  atrMatrix:        [],
  club97:           [],
  stockbeeResults:  {},
  industries:       [],
  stages:           null,
  grades:           null,

  loading:          false,
  sectionLoading:   {},
  error:            null,
  lastUpdate:       null,
  activeGuruTab:    'qullamaggie',
  activeStockbeeTab: '9m_movers',

  setDashboard: (data) => set({
    breadth:         data.breadth,
    guruResults:     data.guru_results,
    atrMatrix:       data.atr_matrix,
    club97:          data.club97,
    stockbeeResults: data.stockbee,
    industries:      data.industries,
    stages:          data.stages,
    grades:          data.grades,
    lastUpdate:      new Date(),
    error:           null,
  }),

  setGuruTab:        (tab) => set({ activeGuruTab: tab }),
  setStockbeeTab:    (tab) => set({ activeStockbeeTab: tab }),
  setLoading:        (loading) => set({ loading }),
  setSectionLoading: (section, loading) =>
    set((s) => ({ sectionLoading: { ...s.sectionLoading, [section]: loading } })),
  setError:          (error) => set({ error }),
}))
