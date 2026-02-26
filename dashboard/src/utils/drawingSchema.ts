/**
 * Zod schemas for validating drawing data at boundaries
 * (settings load, JSON import, external payloads).
 *
 * Provides safe defaults and migration for old payloads.
 */
import { z } from 'zod'
import type { Drawing } from '@/types/drawing'

// ── Schemas ────────────────────────────────────────────────────────────────

const DrawingPointSchema = z.object({
  time: z.number(),
  price: z.number(),
})

const DrawingOptionsSchema = z.object({
  extendLeft: z.boolean().optional(),
  extendRight: z.boolean().optional(),
}).optional()

const DrawingTypeSchema = z.enum(['horizontal_line', 'trendline', 'fibonacci'])

const DrawingSchema = z.object({
  id: z.string().min(1),
  type: DrawingTypeSchema,
  symbol: z.string().min(1),
  timeframe: z.string().default('1d'),
  color: z.string().default('#4f91ff'),
  points: z.array(DrawingPointSchema),
  visible: z.boolean().default(true),
  locked: z.boolean().default(false),
  label: z.string().optional(),
  options: DrawingOptionsSchema,
}).refine(
  (d) => {
    if (d.type === 'horizontal_line') return d.points.length === 1
    return d.points.length === 2
  },
  { message: 'Invalid point count for drawing type' },
)

/** Schema for the entire drawings map (keyed by symbol_timeframe). */
const DrawingsMapSchema = z.record(z.string(), z.array(DrawingSchema))

/** Export format with version for future migration. */
const DrawingsExportSchema = z.object({
  version: z.number().default(1),
  drawings: DrawingsMapSchema,
})

// ── Validation functions ──────────────────────────────────────────────────

/**
 * Validate and migrate a drawings map from settings or import.
 * Returns a clean map; invalid entries are silently dropped.
 */
export function validateDrawingsMap(
  data: unknown,
): { valid: Record<string, Drawing[]>; errors: string[] } {
  const errors: string[] = []
  const valid: Record<string, Drawing[]> = {}

  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    return { valid, errors: ['Drawings data is not an object'] }
  }

  for (const [key, value] of Object.entries(data as Record<string, unknown>)) {
    if (!Array.isArray(value)) {
      errors.push(`Key "${key}" is not an array`)
      continue
    }

    const validDrawings: Drawing[] = []
    for (let i = 0; i < value.length; i++) {
      const result = DrawingSchema.safeParse(value[i])
      if (result.success) {
        validDrawings.push(result.data as Drawing)
      } else {
        errors.push(`Key "${key}" index ${i}: ${result.error.issues[0]?.message ?? 'unknown error'}`)
      }
    }
    if (validDrawings.length > 0) {
      valid[key] = validDrawings
    }
  }

  return { valid, errors }
}

/**
 * Validate an export payload (with version header).
 */
export function validateDrawingsExport(
  data: unknown,
): { valid: { version: number; drawings: Record<string, Drawing[]> } | null; errors: string[] } {
  const result = DrawingsExportSchema.safeParse(data)
  if (result.success) {
    // Still validate inner drawings
    const inner = validateDrawingsMap(result.data.drawings)
    return {
      valid: { version: result.data.version, drawings: inner.valid },
      errors: inner.errors,
    }
  }
  return { valid: null, errors: result.error.issues.map((i) => i.message) }
}

/**
 * Validate a single drawing (for add/update operations).
 */
export function validateDrawing(data: unknown): Drawing | null {
  const result = DrawingSchema.safeParse(data)
  return result.success ? (result.data as Drawing) : null
}
