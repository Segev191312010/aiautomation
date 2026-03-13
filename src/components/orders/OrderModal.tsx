/**
 * OrderModal — Global order placement dialog.
 * Controlled by useUIStore (showOrderModal, orderModalSymbol).
 */
import React, { useState } from 'react'
import clsx from 'clsx'
import { useUIStore } from '@/store'
import { placeManualOrder } from '@/services/api'
import { addToast } from '@/components/notifications/ToastContainer'

export default function OrderModal() {
  const { showOrderModal, orderModalSymbol, closeOrderModal } = useUIStore()
  const [symbol, setSymbol] = useState(orderModalSymbol || '')
  const [qty, setQty] = useState(1)
  const [action, setAction] = useState<'BUY' | 'SELL'>('BUY')
  const [orderType, setOrderType] = useState<'MKT' | 'LMT'>('MKT')
  const [limitPrice, setLimitPrice] = useState('')
  const [busy, setBusy] = useState(false)

  // Reset form when modal opens with a new symbol
  React.useEffect(() => {
    if (showOrderModal) {
      setSymbol(orderModalSymbol || '')
      setQty(1)
      setAction('BUY')
      setOrderType('MKT')
      setLimitPrice('')
    }
  }, [showOrderModal, orderModalSymbol])

  if (!showOrderModal) return null

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!symbol.trim() || qty <= 0) return
    setBusy(true)
    try {
      const body: Parameters<typeof placeManualOrder>[0] = {
        symbol: symbol.toUpperCase(),
        action,
        quantity: qty,
        order_type: orderType,
      }
      if (orderType === 'LMT' && limitPrice) {
        body.limit_price = Number(limitPrice)
      }
      const r = await placeManualOrder(body)
      addToast({ type: 'success', title: 'Order Placed', message: r.message ?? `${action} ${qty} ${symbol.toUpperCase()}` })
      closeOrderModal()
    } catch (err: unknown) {
      addToast({ type: 'error', title: 'Order Failed', message: err instanceof Error ? err.message : 'Unknown error' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={closeOrderModal} />

      {/* Modal */}
      <form
        onSubmit={handleSubmit}
        className="relative z-50 bg-terminal-elevated border border-terminal-border rounded-lg shadow-terminal p-6 w-full max-w-md space-y-4"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-mono font-semibold text-terminal-text">Place Order</h2>
          <button type="button" onClick={closeOrderModal} className="text-terminal-ghost hover:text-terminal-dim text-sm">
            x
          </button>
        </div>

        {/* Symbol */}
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-mono text-terminal-ghost uppercase">Symbol</label>
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder="AAPL"
            className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-3 py-2 text-terminal-text focus:border-terminal-blue focus:outline-none uppercase"
          />
        </div>

        {/* Side buttons */}
        <div className="flex gap-2">
          {(['BUY', 'SELL'] as const).map((a) => (
            <button
              key={a}
              type="button"
              onClick={() => setAction(a)}
              className={clsx(
                'flex-1 text-sm font-mono py-2 rounded border transition-colors font-semibold',
                action === a && a === 'BUY'
                  ? 'border-terminal-green/50 bg-terminal-green/15 text-terminal-green'
                  : action === a && a === 'SELL'
                  ? 'border-terminal-red/50 bg-terminal-red/15 text-terminal-red'
                  : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim',
              )}
            >
              {a}
            </button>
          ))}
        </div>

        {/* Qty + Type */}
        <div className="flex gap-3">
          <div className="flex flex-col gap-1 flex-1">
            <label className="text-[10px] font-mono text-terminal-ghost uppercase">Quantity</label>
            <input
              type="number"
              min={1}
              value={qty}
              onChange={(e) => setQty(Number(e.target.value))}
              className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-3 py-2 text-terminal-text focus:border-terminal-blue focus:outline-none"
            />
          </div>
          <div className="flex flex-col gap-1 w-28">
            <label className="text-[10px] font-mono text-terminal-ghost uppercase">Type</label>
            <select
              value={orderType}
              onChange={(e) => setOrderType(e.target.value as 'MKT' | 'LMT')}
              className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-3 py-2 text-terminal-text focus:border-terminal-blue focus:outline-none"
            >
              <option value="MKT">Market</option>
              <option value="LMT">Limit</option>
            </select>
          </div>
        </div>

        {/* Limit price */}
        {orderType === 'LMT' && (
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-terminal-ghost uppercase">Limit Price</label>
            <input
              type="number"
              step="0.01"
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
              placeholder="0.00"
              className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-3 py-2 text-terminal-text focus:border-terminal-blue focus:outline-none"
            />
          </div>
        )}

        {/* Submit */}
        <button
          type="submit"
          disabled={busy || !symbol.trim()}
          className={clsx(
            'w-full text-sm font-mono py-2.5 rounded border font-semibold transition-colors disabled:opacity-40',
            action === 'BUY'
              ? 'border-terminal-green/50 bg-terminal-green/20 text-terminal-green hover:bg-terminal-green/30'
              : 'border-terminal-red/50 bg-terminal-red/20 text-terminal-red hover:bg-terminal-red/30',
          )}
        >
          {busy ? 'Placing...' : `${action} ${qty} ${symbol.toUpperCase() || '...'}`}
        </button>
      </form>
    </div>
  )
}
