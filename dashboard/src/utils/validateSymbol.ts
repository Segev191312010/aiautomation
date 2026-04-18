const SYMBOL_RE = /^[A-Z0-9\-.]{1,20}$/

export interface SymbolValidation {
  ok: boolean
  reason?: string
}

export function validateSymbol(raw: string): SymbolValidation {
  if (!raw) return { ok: false, reason: 'Symbol is required' }
  if (raw.length > 20) return { ok: false, reason: 'Symbol must be 20 characters or fewer' }
  if (!SYMBOL_RE.test(raw)) return { ok: false, reason: 'Use uppercase letters, digits, "-" or "." only' }
  return { ok: true }
}
