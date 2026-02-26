/**
 * drawingEngine — pure math & Canvas rendering for chart drawings.
 *
 * No React, no DOM globals. All coordinate conversions are injected
 * as functions so this module stays testable and reusable.
 */
import type { Drawing, DrawingPoint, DrawingType, HitTestResult } from '@/types/drawing'
import type { OHLCVBar } from '@/types'

// ── Constants ──────────────────────────────────────────────────────────────

export const FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0] as const

export const FIB_LABELS = ['0%', '23.6%', '38.2%', '50%', '61.8%', '78.6%', '100%'] as const

export const DEFAULT_DRAWING_COLOR = '#4f91ff'

export const DRAWING_COLORS = [
  '#4f91ff',  // blue  (terminal-blue)
  '#00e07a',  // green (terminal-green)
  '#ff3d5a',  // red   (terminal-red)
  '#f59e0b',  // amber
  '#a78bfa',  // purple
  '#38bdf8',  // cyan
  '#f472b6',  // pink
  '#dce8f5',  // white (terminal-text)
] as const

export const HIT_TOLERANCE = 8 // pixels

// ── Geometry ───────────────────────────────────────────────────────────────

/** Perpendicular distance from point (px,py) to segment (x1,y1)-(x2,y2). */
export function pointToLineDistance(
  px: number, py: number,
  x1: number, y1: number,
  x2: number, y2: number,
): number {
  const dx = x2 - x1
  const dy = y2 - y1
  const lenSq = dx * dx + dy * dy
  if (lenSq === 0) return Math.hypot(px - x1, py - y1)
  const t = Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / lenSq))
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
}

/** Distance from point to an infinite line (not segment-clamped). */
export function pointToInfiniteLineDistance(
  px: number, py: number,
  x1: number, y1: number,
  x2: number, y2: number,
): number {
  const dx = x2 - x1
  const dy = y2 - y1
  const len = Math.hypot(dx, dy)
  if (len === 0) return Math.hypot(px - x1, py - y1)
  return Math.abs(dy * px - dx * py + x2 * y1 - y2 * x1) / len
}

// ── Hit Testing ────────────────────────────────────────────────────────────

export function hitTestHorizontalLine(
  mouseX: number, mouseY: number,
  priceY: number,
  canvasWidth: number,
  handleX?: number,
): { hit: boolean; part: 'line' | 'handle' | 'label' } {
  if (handleX !== undefined && Math.hypot(mouseX - handleX, mouseY - priceY) <= HIT_TOLERANCE) {
    return { hit: true, part: 'handle' }
  }
  // Label area on right edge
  if (mouseX >= canvasWidth - 80 && Math.abs(mouseY - priceY) <= 12) {
    return { hit: true, part: 'label' }
  }
  if (Math.abs(mouseY - priceY) <= HIT_TOLERANCE) {
    return { hit: true, part: 'line' }
  }
  return { hit: false, part: 'line' }
}

export function hitTestTrendline(
  mouseX: number, mouseY: number,
  x1: number, y1: number,
  x2: number, y2: number,
  extended?: { left?: boolean; right?: boolean },
): { hit: boolean; part: 'line' | 'handle'; handleIndex?: number } {
  // Check handles first
  if (Math.hypot(mouseX - x1, mouseY - y1) <= HIT_TOLERANCE) {
    return { hit: true, part: 'handle', handleIndex: 0 }
  }
  if (Math.hypot(mouseX - x2, mouseY - y2) <= HIT_TOLERANCE) {
    return { hit: true, part: 'handle', handleIndex: 1 }
  }
  // Line distance
  const dist = (extended?.left || extended?.right)
    ? pointToInfiniteLineDistance(mouseX, mouseY, x1, y1, x2, y2)
    : pointToLineDistance(mouseX, mouseY, x1, y1, x2, y2)
  if (dist <= HIT_TOLERANCE) {
    return { hit: true, part: 'line' }
  }
  return { hit: false, part: 'line' }
}

export function hitTestFibonacci(
  mouseX: number, mouseY: number,
  levelYs: Array<{ y: number; price: number }>,
  canvasWidth: number,
): { hit: boolean; part: 'line' | 'label'; levelIndex?: number } {
  for (let i = 0; i < levelYs.length; i++) {
    const { y } = levelYs[i]
    // Label area
    if (mouseX >= canvasWidth - 120 && Math.abs(mouseY - y) <= 10) {
      return { hit: true, part: 'label', levelIndex: i }
    }
    if (Math.abs(mouseY - y) <= HIT_TOLERANCE) {
      return { hit: true, part: 'line', levelIndex: i }
    }
  }
  return { hit: false, part: 'line' }
}

/**
 * Test all drawings for a hit at the given mouse position.
 * Returns the first hit (topmost in drawing order).
 */
export function hitTestDrawings(
  mouseX: number, mouseY: number,
  drawings: Drawing[],
  priceToY: (p: number) => number | null,
  timeToX: (t: number) => number | null,
  canvasWidth: number,
): HitTestResult | null {
  // Iterate in reverse so topmost (last-drawn) is checked first
  for (let i = drawings.length - 1; i >= 0; i--) {
    const d = drawings[i]
    if (!d.visible) continue

    switch (d.type) {
      case 'horizontal_line': {
        const y = priceToY(d.points[0].price)
        if (y === null) continue
        const res = hitTestHorizontalLine(mouseX, mouseY, y, canvasWidth)
        if (res.hit) return { hit: true, drawingId: d.id, part: res.part }
        break
      }
      case 'trendline': {
        if (d.points.length < 2) continue
        const x1 = timeToX(d.points[0].time)
        const y1 = priceToY(d.points[0].price)
        const x2 = timeToX(d.points[1].time)
        const y2 = priceToY(d.points[1].price)
        if (x1 === null || y1 === null || x2 === null || y2 === null) continue
        const res = hitTestTrendline(mouseX, mouseY, x1, y1, x2, y2, {
          left: d.options?.extendLeft,
          right: d.options?.extendRight,
        })
        if (res.hit) return { hit: true, drawingId: d.id, part: res.part, handleIndex: res.handleIndex }
        break
      }
      case 'fibonacci': {
        if (d.points.length < 2) continue
        const high = Math.max(d.points[0].price, d.points[1].price)
        const low = Math.min(d.points[0].price, d.points[1].price)
        const levels = calcFibLevels(high, low)
        const levelYs: Array<{ y: number; price: number }> = []
        for (const lv of levels) {
          const y = priceToY(lv.price)
          if (y !== null) levelYs.push({ y, price: lv.price })
        }
        const res = hitTestFibonacci(mouseX, mouseY, levelYs, canvasWidth)
        if (res.hit) return { hit: true, drawingId: d.id, part: res.part }
        break
      }
    }
  }
  return null
}

// ── Fibonacci ──────────────────────────────────────────────────────────────

export interface FibLevel {
  level: number
  price: number
  label: string
  isGoldenZone: boolean
}

/**
 * Calculate Fibonacci retracement levels given two prices.
 * Level 0 = high (anchor A), Level 1 = low (anchor B).
 * Golden zone = 0.382–0.618.
 */
export function calcFibLevels(high: number, low: number): FibLevel[] {
  return FIB_LEVELS.map((level, i) => ({
    level,
    price: high - (high - low) * level,
    label: FIB_LABELS[i],
    isGoldenZone: level >= 0.382 && level <= 0.618,
  }))
}

// ── Snap to Candle ─────────────────────────────────────────────────────────

/**
 * Snap a price to the nearest OHLC value of the nearest candle.
 * Returns the snapped price. If no candles, returns the original price.
 */
export function snapToCandle(
  price: number,
  time: number,
  candles: OHLCVBar[],
): { price: number; time: number } {
  if (!candles.length) return { price, time }

  // Find nearest candle by time
  let nearestIdx = 0
  let minTimeDiff = Infinity
  for (let i = 0; i < candles.length; i++) {
    const diff = Math.abs(candles[i].time - time)
    if (diff < minTimeDiff) {
      minTimeDiff = diff
      nearestIdx = i
    }
  }

  const candle = candles[nearestIdx]
  const ohlcValues = [candle.open, candle.high, candle.low, candle.close]

  // Find closest OHLC value to the given price
  let best = ohlcValues[0]
  let bestDiff = Math.abs(price - best)
  for (let j = 1; j < ohlcValues.length; j++) {
    const diff = Math.abs(price - ohlcValues[j])
    if (diff < bestDiff) {
      bestDiff = diff
      best = ohlcValues[j]
    }
  }

  return { price: best, time: candle.time }
}

// ── Measurement helpers ────────────────────────────────────────────────────

export interface TrendlineMeasurement {
  priceDelta: number
  priceDeltaPct: number
  barCount: number
  angle: number // degrees
}

export function measureTrendline(
  p1: DrawingPoint,
  p2: DrawingPoint,
  barSeconds: number,
): TrendlineMeasurement {
  const priceDelta = p2.price - p1.price
  const priceDeltaPct = p1.price !== 0 ? (priceDelta / p1.price) * 100 : 0
  const barCount = barSeconds > 0 ? Math.round(Math.abs(p2.time - p1.time) / barSeconds) : 0
  // Angle in pixel-space must be computed at render time; here we compute
  // a data-space angle for display (rise/run in price/bars)
  const run = barCount || 1
  const angle = Math.atan2(priceDelta, run) * (180 / Math.PI)
  return { priceDelta, priceDeltaPct, barCount, angle }
}

// ── Rendering ──────────────────────────────────────────────────────────────

const FONT = '10px "JetBrains Mono", monospace'
const FONT_SMALL = '9px "JetBrains Mono", monospace'
const TERMINAL_BG = '#080d18'
const LOCK_COLOR = '#5f7a9d'

type CoordFn = (v: number) => number | null

export interface RenderContext {
  priceToY: CoordFn
  timeToX: CoordFn
  canvasWidth: number
  canvasHeight: number
  selectedId: string | null
  hoveredId: string | null
  barSeconds: number
}

export function renderAllDrawings(
  ctx: CanvasRenderingContext2D,
  drawings: Drawing[],
  rc: RenderContext,
): void {
  ctx.clearRect(0, 0, rc.canvasWidth, rc.canvasHeight)

  for (const d of drawings) {
    if (!d.visible) continue
    const isSelected = d.id === rc.selectedId
    const isHovered = d.id === rc.hoveredId

    ctx.save()
    ctx.strokeStyle = d.locked ? LOCK_COLOR : d.color
    ctx.fillStyle = d.locked ? LOCK_COLOR : d.color
    ctx.lineWidth = isSelected ? 2.5 : isHovered ? 2 : 1.5
    ctx.font = FONT

    switch (d.type) {
      case 'horizontal_line':
        renderHorizontalLine(ctx, d, rc, isSelected, isHovered)
        break
      case 'trendline':
        renderTrendline(ctx, d, rc, isSelected, isHovered)
        break
      case 'fibonacci':
        renderFibonacci(ctx, d, rc, isSelected)
        break
    }

    // Lock indicator
    if (d.locked && isSelected) {
      renderLockIcon(ctx, 16, 16)
    }

    ctx.restore()
  }
}

function renderHorizontalLine(
  ctx: CanvasRenderingContext2D,
  d: Drawing,
  rc: RenderContext,
  isSelected: boolean,
  _isHovered: boolean,
): void {
  const price = d.points[0].price
  const y = rc.priceToY(price)
  if (y === null) return

  // Dashed line across full width
  ctx.beginPath()
  ctx.setLineDash([6, 3])
  ctx.moveTo(0, y)
  ctx.lineTo(rc.canvasWidth, y)
  ctx.stroke()
  ctx.setLineDash([])

  // Price label on right
  const labelText = d.label || price.toFixed(2)
  const textWidth = ctx.measureText(labelText).width
  const labelX = rc.canvasWidth - textWidth - 12
  const labelY = y

  ctx.fillStyle = d.locked ? LOCK_COLOR : d.color
  ctx.globalAlpha = 0.85
  ctx.fillRect(labelX - 4, labelY - 11, textWidth + 8, 14)
  ctx.globalAlpha = 1
  ctx.fillStyle = TERMINAL_BG
  ctx.fillText(labelText, labelX, labelY)

  // Selection handle at center
  if (isSelected) {
    drawHandle(ctx, rc.canvasWidth / 2, y, d.color)
  }
}

function renderTrendline(
  ctx: CanvasRenderingContext2D,
  d: Drawing,
  rc: RenderContext,
  isSelected: boolean,
  _isHovered: boolean,
): void {
  if (d.points.length < 2) return
  const [p1, p2] = d.points

  const x1 = rc.timeToX(p1.time)
  const y1 = rc.priceToY(p1.price)
  const x2 = rc.timeToX(p2.time)
  const y2 = rc.priceToY(p2.price)
  if (x1 === null || y1 === null || x2 === null || y2 === null) return

  ctx.beginPath()
  const extL = d.options?.extendLeft
  const extR = d.options?.extendRight

  if (extL || extR) {
    const dx = x2 - x1
    const dy = y2 - y1
    const len = Math.hypot(dx, dy)
    if (len > 0) {
      const scale = Math.max(rc.canvasWidth, rc.canvasHeight) * 2 / len
      const startX = extL ? x1 - dx * scale : x1
      const startY = extL ? y1 - dy * scale : y1
      const endX = extR ? x2 + dx * scale : x2
      const endY = extR ? y2 + dy * scale : y2
      ctx.moveTo(startX, startY)
      ctx.lineTo(endX, endY)
    }
  } else {
    ctx.moveTo(x1, y1)
    ctx.lineTo(x2, y2)
  }
  ctx.stroke()

  // Measurement overlay at midpoint
  const meas = measureTrendline(p1, p2, rc.barSeconds)
  const mx = (x1 + x2) / 2
  const my = (y1 + y2) / 2 - 14

  ctx.font = FONT_SMALL
  ctx.fillStyle = d.locked ? LOCK_COLOR : d.color
  ctx.globalAlpha = 0.8
  const sign = meas.priceDelta >= 0 ? '+' : ''
  const measText = `${sign}${meas.priceDelta.toFixed(2)} (${sign}${meas.priceDeltaPct.toFixed(1)}%)  ${meas.barCount} bars`
  ctx.fillText(measText, mx, my)
  ctx.globalAlpha = 1
  ctx.font = FONT

  // Label
  if (d.label) {
    ctx.fillStyle = d.locked ? LOCK_COLOR : d.color
    ctx.fillText(d.label, mx, my - 12)
  }

  // Selection handles
  if (isSelected) {
    drawHandle(ctx, x1, y1, d.color)
    drawHandle(ctx, x2, y2, d.color)
  }
}

function renderFibonacci(
  ctx: CanvasRenderingContext2D,
  d: Drawing,
  rc: RenderContext,
  isSelected: boolean,
): void {
  if (d.points.length < 2) return
  const [p1, p2] = d.points

  const high = Math.max(p1.price, p2.price)
  const low = Math.min(p1.price, p2.price)
  const levels = calcFibLevels(high, low)

  // Golden zone fill (0.382 – 0.618)
  const y382 = rc.priceToY(high - (high - low) * 0.382)
  const y618 = rc.priceToY(high - (high - low) * 0.618)
  if (y382 !== null && y618 !== null) {
    ctx.fillStyle = d.locked ? LOCK_COLOR : d.color
    ctx.globalAlpha = 0.06
    ctx.fillRect(0, Math.min(y382, y618), rc.canvasWidth, Math.abs(y618 - y382))
    ctx.globalAlpha = 1
  }

  // Level lines
  for (const lv of levels) {
    const y = rc.priceToY(lv.price)
    if (y === null) continue

    ctx.beginPath()
    ctx.globalAlpha = lv.level === 0 || lv.level === 1 ? 0.9 : lv.isGoldenZone ? 0.7 : 0.5
    ctx.setLineDash(lv.level === 0.5 ? [4, 4] : [])
    ctx.moveTo(0, y)
    ctx.lineTo(rc.canvasWidth, y)
    ctx.stroke()
    ctx.setLineDash([])
    ctx.globalAlpha = 1

    // Level label + price on right side
    ctx.font = FONT_SMALL
    ctx.fillStyle = d.locked ? LOCK_COLOR : d.color
    ctx.globalAlpha = 0.85
    const priceText = `${lv.label}  ${lv.price.toFixed(2)}`
    ctx.fillText(priceText, 8, y - 4)
    ctx.globalAlpha = 1
    ctx.font = FONT
  }

  // User label
  if (d.label) {
    const yTop = rc.priceToY(high)
    if (yTop !== null) {
      ctx.fillStyle = d.locked ? LOCK_COLOR : d.color
      ctx.fillText(d.label, 8, yTop - 16)
    }
  }

  // Selection handles at anchor points
  if (isSelected) {
    const x1 = rc.timeToX(p1.time)
    const x2 = rc.timeToX(p2.time)
    const yH = rc.priceToY(high)
    const yL = rc.priceToY(low)
    if (x1 !== null && yH !== null) drawHandle(ctx, x1, yH, d.color)
    if (x2 !== null && yL !== null) drawHandle(ctx, x2, yL, d.color)
  }
}

function drawHandle(ctx: CanvasRenderingContext2D, x: number, y: number, color: string): void {
  ctx.save()
  ctx.fillStyle = TERMINAL_BG
  ctx.strokeStyle = color
  ctx.lineWidth = 1.5
  ctx.setLineDash([])
  ctx.beginPath()
  ctx.arc(x, y, 4, 0, Math.PI * 2)
  ctx.fill()
  ctx.stroke()
  ctx.restore()
}

function renderLockIcon(ctx: CanvasRenderingContext2D, x: number, y: number): void {
  ctx.save()
  ctx.fillStyle = LOCK_COLOR
  ctx.font = '12px sans-serif'
  ctx.fillText('🔒', x, y)
  ctx.restore()
}

// ── Preview rendering (in-progress drawings) ──────────────────────────────

export function renderPreview(
  ctx: CanvasRenderingContext2D,
  type: DrawingType,
  color: string,
  anchor: { x: number; y: number; price: number },
  cursor: { x: number; y: number; price: number },
  rc: RenderContext,
): void {
  ctx.save()
  ctx.strokeStyle = color
  ctx.fillStyle = color
  ctx.lineWidth = 1.5
  ctx.globalAlpha = 0.6
  ctx.setLineDash([4, 4])
  ctx.font = FONT_SMALL

  switch (type) {
    case 'horizontal_line': {
      ctx.beginPath()
      ctx.moveTo(0, cursor.y)
      ctx.lineTo(rc.canvasWidth, cursor.y)
      ctx.stroke()
      ctx.globalAlpha = 0.8
      ctx.setLineDash([])
      ctx.fillText(cursor.price.toFixed(2), rc.canvasWidth - 70, cursor.y - 4)
      break
    }
    case 'trendline': {
      ctx.beginPath()
      ctx.moveTo(anchor.x, anchor.y)
      ctx.lineTo(cursor.x, cursor.y)
      ctx.stroke()
      break
    }
    case 'fibonacci': {
      const high = Math.max(anchor.price, cursor.price)
      const low = Math.min(anchor.price, cursor.price)
      const levels = calcFibLevels(high, low)

      for (const lv of levels) {
        const y = rc.priceToY(lv.price)
        if (y === null) continue
        ctx.beginPath()
        ctx.moveTo(0, y)
        ctx.lineTo(rc.canvasWidth, y)
        ctx.stroke()
        ctx.setLineDash([])
        ctx.globalAlpha = 0.5
        ctx.fillText(`${lv.label}  ${lv.price.toFixed(2)}`, 8, y - 4)
        ctx.globalAlpha = 0.6
        ctx.setLineDash([4, 4])
      }
      break
    }
  }

  // Snap marker
  ctx.setLineDash([])
  ctx.globalAlpha = 0.9
  ctx.beginPath()
  ctx.arc(cursor.x, cursor.y, 3, 0, Math.PI * 2)
  ctx.fill()

  ctx.restore()
}
