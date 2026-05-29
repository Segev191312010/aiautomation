/**
 * Session-anchored VWAP — resets the cumulative accumulator at each US equity
 * session open (09:30 America/New_York), DST-aware.
 *
 * The plain calcVWAP() in indicators.ts is cumulative across the whole series,
 * which is only meaningful intraday. For multi-day intraday charts a single
 * running VWAP drifts and loses its anchor; traders expect VWAP to restart at
 * each regular-session open. This module derives the ET wall-clock for every
 * bar via Intl.DateTimeFormat (so DST transitions are handled correctly) and
 * resets cumulative typical-price·volume / volume at the 09:30 boundary.
 *
 * Bars before 09:30 ET (pre-market) anchor to the prior 00:00–09:30 segment;
 * each new calendar day in ET that contains a >=09:30 bar starts a fresh
 * accumulator. For daily/weekly bars (one bar per day) every bar lands in its
 * own session, so this degrades gracefully to a per-bar VWAP == typical price.
 */
import type { OHLCVBar } from '@/types'
import type { LinePoint } from '@/utils/indicators'

// Reusable formatter — constructing Intl.DateTimeFormat per bar is expensive.
const ET_FORMATTER = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  year:   'numeric',
  month:  '2-digit',
  day:    '2-digit',
  hour:   '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

interface EtParts {
  /** Stable calendar-day key in ET, e.g. "2026-05-29". */
  dayKey: string
  /** Minutes since ET midnight, e.g. 09:30 → 570. */
  minutes: number
}

/** Decompose a Unix-second timestamp into its ET calendar day + minutes-of-day. */
function etParts(timeSec: number): EtParts {
  const parts = ET_FORMATTER.formatToParts(timeSec * 1000)
  let year = '', month = '', day = '', hour = '0', minute = '0'
  for (const p of parts) {
    if (p.type === 'year') year = p.value
    else if (p.type === 'month') month = p.value
    else if (p.type === 'day') day = p.value
    else if (p.type === 'hour') hour = p.value
    else if (p.type === 'minute') minute = p.value
  }
  return {
    dayKey: `${year}-${month}-${day}`,
    minutes: parseInt(hour, 10) * 60 + parseInt(minute, 10),
  }
}

const SESSION_OPEN_MINUTES = 9 * 60 + 30 // 09:30 ET

/**
 * Cumulative VWAP that resets at every 09:30 ET regular-session open.
 *
 * The anchor advances when either (a) the ET calendar day changes, or (b) a bar
 * at/after 09:30 ET is seen for a day whose accumulator is still on the
 * pre-market segment. Within a session the value is the standard volume-weighted
 * mean of typical price ((H+L+C)/3); zero-volume windows fall back to close.
 */
export function calcSessionVWAP(bars: OHLCVBar[]): LinePoint[] {
  const out: LinePoint[] = []
  if (!bars.length) return out

  let cumTPV = 0
  let cumVol = 0
  let curDayKey = ''
  let sessionOpened = false

  for (const b of bars) {
    const { dayKey, minutes } = etParts(b.time)
    const atOrAfterOpen = minutes >= SESSION_OPEN_MINUTES

    if (dayKey !== curDayKey) {
      // New ET day → start a fresh pre-market accumulator.
      curDayKey = dayKey
      cumTPV = 0
      cumVol = 0
      sessionOpened = atOrAfterOpen
    } else if (atOrAfterOpen && !sessionOpened) {
      // First regular-session bar of the day → re-anchor at 09:30.
      cumTPV = 0
      cumVol = 0
      sessionOpened = true
    }

    const tp = (b.high + b.low + b.close) / 3
    cumTPV += tp * b.volume
    cumVol += b.volume
    out.push({
      time: b.time,
      value: +(cumVol > 0 ? cumTPV / cumVol : b.close).toFixed(4),
    })
  }

  return out
}
