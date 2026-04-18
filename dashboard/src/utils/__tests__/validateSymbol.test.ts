import { describe, expect, it } from 'vitest'
import { validateSymbol } from '@/utils/validateSymbol'

describe('validateSymbol', () => {
  describe('accepts valid symbols', () => {
    it.each([
      ['AAPL'],
      ['SPY'],
      ['BRK.B'],
      ['BTC-USD'],
      ['A'],
      ['Q'],
      ['TSLA'],
      ['BF.B'],
      ['12345678901234567890'], // 20 chars (boundary)
    ])('accepts %s', (sym) => {
      expect(validateSymbol(sym).ok).toBe(true)
    })
  })

  describe('rejects invalid symbols', () => {
    it('rejects empty string', () => {
      const result = validateSymbol('')
      expect(result.ok).toBe(false)
      expect(result.reason).toBe('Symbol is required')
    })

    it('rejects lowercase', () => {
      expect(validateSymbol('aapl').ok).toBe(false)
      expect(validateSymbol('Aapl').ok).toBe(false)
    })

    it('rejects invalid characters', () => {
      expect(validateSymbol('AAPL*').ok).toBe(false)
      expect(validateSymbol('AA PL').ok).toBe(false)
      expect(validateSymbol('AAPL!').ok).toBe(false)
      expect(validateSymbol('$SPY').ok).toBe(false)
      expect(validateSymbol('AAPL/USD').ok).toBe(false)
    })

    it('rejects overly long symbols', () => {
      const result = validateSymbol('123456789012345678901') // 21 chars
      expect(result.ok).toBe(false)
      expect(result.reason).toBe('Symbol must be 20 characters or fewer')
    })
  })
})
