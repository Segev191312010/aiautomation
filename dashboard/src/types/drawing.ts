// ── Drawing types ──────────────────────────────────────────────────────────

/** Tool types available for drawing on chart. */
export type DrawingType = 'horizontal_line' | 'trendline' | 'fibonacci'

/**
 * A point on the chart in data coordinates.
 * Time is always UNIX seconds (matches OHLCVBar.time).
 */
export interface DrawingPoint {
  time: number
  price: number
}

/** Per-tool options. */
export interface DrawingOptions {
  /** Trendline: extend line to the left beyond anchor A. */
  extendLeft?: boolean
  /** Trendline: extend line to the right beyond anchor B. */
  extendRight?: boolean
}

/**
 * A persisted drawing on the chart.
 *
 * Points invariant:
 *   horizontal_line  →  exactly 1 point (only price matters)
 *   trendline        →  exactly 2 points (anchor A, anchor B)
 *   fibonacci        →  exactly 2 points (anchor A = level 0, anchor B = level 1)
 */
export interface Drawing {
  id: string
  type: DrawingType
  symbol: string
  /** Timeframe interval string, e.g. '1d', '1h'. */
  timeframe: string
  color: string
  points: DrawingPoint[]
  visible: boolean
  locked: boolean
  /** User-editable label. Defaults to price value if absent. */
  label?: string
  /** Per-tool options (extend, etc.). */
  options?: DrawingOptions
}

/** Transient UI state for the active drawing tool. */
export interface DrawingToolState {
  activeTool: DrawingType | null
  selectedDrawingId: string | null
  drawingColor: string
}

/** Hit-test result from drawingEngine. */
export interface HitTestResult {
  hit: boolean
  drawingId: string
  /** Which part was hit. */
  part: 'line' | 'handle' | 'label'
  /** For handle hits, which handle index (0 = anchor A, 1 = anchor B). */
  handleIndex?: number
}
