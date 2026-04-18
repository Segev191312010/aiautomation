import { useState } from 'react'
import clsx from 'clsx'
import { useToast } from '@/components/ui/ToastProvider'
import { placeManualOrder } from '@/services/api'
import { IconLightning } from '@/components/icons'
import { validateSymbol } from '@/utils/validateSymbol'
import ConfirmModal from '@/components/common/ConfirmModal'

export function QuickOrderForm() {
  const toast = useToast()
  const [sym,    setSym]    = useState('')
  const [qty,    setQty]    = useState(1)
  const [action, setAction] = useState<'BUY' | 'SELL'>('BUY')
  const [status, setStatus] = useState('')
  const [busy,   setBusy]   = useState(false)
  const [pendingOrder, setPendingOrder] = useState<null | {
    symbol: string
    action: 'BUY' | 'SELL'
    quantity: number
  }>(null)

  const normalizedSym = sym.trim().toUpperCase()
  const symValidation = validateSymbol(normalizedSym)
  const symTouched = sym.length > 0

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!symValidation.ok || qty <= 0) return
    setPendingOrder({ symbol: normalizedSym, action, quantity: qty })
  }

  const handleConfirm = async () => {
    const order = pendingOrder
    if (!order) return
    setPendingOrder(null)
    setBusy(true)
    setStatus('')
    try {
      const r = await placeManualOrder(order)
      const msg = r.message ?? 'Order placed'
      setStatus(msg)
      toast.success(msg)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Order failed'
      setStatus(msg)
      toast.error(msg)
    } finally {
      setBusy(false)
    }
  }

  const isBuy = action === 'BUY'
  const canSubmit = symValidation.ok && qty > 0

  return (
    <div className="flex flex-col gap-5">
      {/* Inputs row */}
      <div className="flex flex-wrap items-end gap-4">
        {/* Symbol */}
        <div className="flex flex-col gap-1.5">
          <label htmlFor="quick-order-symbol" className="text-[11px] font-sans font-medium text-zinc-400 tracking-widest uppercase">
            Symbol
          </label>
          <input
            id="quick-order-symbol"
            value={sym}
            onChange={(e) => setSym(e.target.value)}
            placeholder="AAPL"
            aria-invalid={symTouched && !symValidation.ok}
            aria-describedby={symTouched && !symValidation.ok ? 'quick-order-symbol-error' : undefined}
            className={clsx(
              'w-28 text-sm font-mono bg-zinc-900 border rounded-xl px-3 py-2 text-zinc-100 focus:outline-none uppercase tracking-wider placeholder:text-zinc-500/50',
              symTouched && !symValidation.ok
                ? 'border-red-500/60 focus:border-red-500'
                : 'border-zinc-800 focus:border-indigo-600/50',
            )}
          />
          {symTouched && !symValidation.ok && symValidation.reason && (
            <span id="quick-order-symbol-error" role="alert" className="text-[10px] font-sans text-red-400">
              {symValidation.reason}
            </span>
          )}
        </div>

        {/* Quantity */}
        <div className="flex flex-col gap-1.5">
          <label className="text-[11px] font-sans font-medium text-zinc-400 tracking-widest uppercase">
            Quantity
          </label>
          <input
            type="number"
            min={1}
            value={qty}
            onChange={(e) => setQty(Number(e.target.value))}
            className="w-24 text-sm font-mono bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2 text-zinc-100 focus:border-indigo-600/50 focus:outline-none"
          />
        </div>

        {/* Side toggle */}
        <div className="flex flex-col gap-1.5">
          <label className="text-[11px] font-sans font-medium text-zinc-400 tracking-widest uppercase">
            Side
          </label>
          <div className="flex rounded-xl overflow-hidden border border-zinc-800">
            {(['BUY', 'SELL'] as const).map((a) => (
              <button
                key={a}
                type="button"
                onClick={() => setAction(a)}
                className={clsx(
                  'text-sm font-sans font-semibold px-5 py-2 transition-all duration-150',
                  action === a && a === 'BUY'
                    ? 'bg-emerald-500/20 text-emerald-400 border-r border-zinc-800'
                    : action === a && a === 'SELL'
                    ? 'bg-red-500/20 text-red-400'
                    : a === 'BUY'
                    ? 'text-zinc-500 hover:text-zinc-400 bg-transparent border-r border-zinc-800'
                    : 'text-zinc-500 hover:text-zinc-400 bg-transparent',
                )}
              >
                {a}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Order preview */}
      {symValidation.ok && qty > 0 && (
        <div className={clsx(
          'flex items-center gap-2 px-4 py-2.5 rounded-xl border text-xs font-mono',
          isBuy
            ? 'border-emerald-500/20 bg-emerald-500/[0.05] text-emerald-400/80'
            : 'border-red-500/20 bg-red-500/[0.05] text-red-400/80',
        )}>
          <span className="opacity-60">Preview:</span>
          <span className="font-semibold">{isBuy ? 'BUY' : 'SELL'}</span>
          <span className="text-zinc-400">{qty} share{qty !== 1 ? 's' : ''} of</span>
          <span className="font-semibold text-zinc-100">{normalizedSym}</span>
          <span className="text-zinc-500 ml-1">— Market Order</span>
        </div>
      )}

      {/* Submit row */}
      <form onSubmit={handleSubmit} className="flex items-center gap-3 flex-wrap">
        <button
          type="submit"
          disabled={busy || !canSubmit}
          className={clsx(
            'flex items-center gap-2 text-sm font-sans font-semibold px-6 py-2.5 rounded-xl transition-all duration-150',
            'disabled:opacity-40 disabled:cursor-not-allowed',
            isBuy
              ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/25 hover:border-emerald-500/50'
              : 'bg-red-500/15 text-red-400 border border-red-500/30 hover:bg-red-500/25 hover:border-red-500/50',
          )}
        >
          <IconLightning className="w-3.5 h-3.5" />
          {busy ? 'Placing…' : `Place ${action} Order`}
        </button>
        {status && (
          <span className="text-[11px] font-sans text-zinc-400">{status}</span>
        )}
      </form>

      <ConfirmModal
        open={pendingOrder !== null}
        title={`Confirm ${pendingOrder?.action ?? action} order`}
        summary={pendingOrder ? [
          { label: 'Symbol', value: pendingOrder.symbol },
          { label: 'Side',   value: pendingOrder.action, tone: pendingOrder.action === 'BUY' ? 'success' : 'danger' },
          { label: 'Qty',    value: pendingOrder.quantity },
          { label: 'Type',   value: 'Market' },
        ] : []}
        confirmLabel={`Place ${pendingOrder?.action ?? action}`}
        onConfirm={handleConfirm}
        onCancel={() => setPendingOrder(null)}
      />
    </div>
  )
}
