/**
 * BotToggle - "Automated Trading" master switch with live cycle stats.
 */
import React, { useState } from 'react'
import clsx from 'clsx'
import ConfirmModal from '@/components/common/ConfirmModal'
import { useBotStore } from '@/store'
import { startBot, stopBot } from '@/services/api'

function fmtTime(iso: string | null): string {
  if (!iso) return '--'
  try {
    return new Date(iso).toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
  } catch {
    return '--'
  }
}

export default function BotToggle() {
  const botRunning = useBotStore((s) => s.botRunning)
  const ibkrConnected = useBotStore((s) => s.ibkrConnected)
  const simMode = useBotStore((s) => s.simMode)
  const autopilotMode = useBotStore((s) => s.autopilotMode)
  const liveTradingEnabled = useBotStore((s) => s.liveTradingEnabled)
  const setBotRunning = useBotStore((s) => s.setBotRunning)
  const cycleStats = useBotStore((s) => s.cycleStats)
  const [busy, setBusy] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)

  const liveGateConfigured = liveTradingEnabled !== null
  const liveGateOpen = simMode || liveTradingEnabled === true
  const liveBlockedByAutopilot = autopilotMode === 'LIVE' && liveGateConfigured && !liveGateOpen
  const toggleDisabled = busy || (!ibkrConnected && !simMode) || liveBlockedByAutopilot

  const runToggle = async (nextRunning: boolean) => {
    setBusy(true)
    try {
      if (nextRunning) {
        await startBot()
        setBotRunning(true)
      } else {
        await stopBot()
        setBotRunning(false)
      }
    } catch (error) {
      console.error(error)
    } finally {
      setBusy(false)
    }
  }

  const handleConfirmStart = async () => {
    setConfirmOpen(false)
    await runToggle(true)
  }

  const handleToggle = async () => {
    if (toggleDisabled) return

    if (botRunning) {
      await runToggle(false)
      return
    }

    if (!simMode && ibkrConnected) {
      setConfirmOpen(true)
      return
    }

    await runToggle(true)
  }

  const hasStats = botRunning && (cycleStats.rulesEnabled > 0 || cycleStats.symbolsScanned > 0)

  return (
    <>
      <div
        className={clsx(
          'shell-panel overflow-hidden',
          botRunning && 'border-[rgba(31,157,104,0.24)] bg-[linear-gradient(135deg,rgba(31,157,104,0.12),transparent_50%),var(--bg-card)]',
        )}
      >
        <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              {botRunning && (
                <span className="relative flex h-2.5 w-2.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--success)] opacity-75" />
                  <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-[var(--success)]" />
                </span>
              )}
              <span className="text-sm font-semibold text-[var(--text-primary)]">Automated Trading</span>
              {simMode && (
                <span className="rounded-full border border-[rgba(245,158,11,0.24)] bg-[rgba(245,158,11,0.12)] px-2 py-0.5 text-[10px] font-mono text-[var(--accent)]">
                  SIM
                </span>
              )}
              {!simMode && !ibkrConnected && (
                <span className="rounded-full border border-[rgba(217,76,61,0.24)] bg-[rgba(217,76,61,0.12)] px-2 py-0.5 text-[10px] font-mono text-[var(--danger)]">
                  IBKR OFFLINE
                </span>
              )}
              {liveBlockedByAutopilot && (
                <span className="rounded-full border border-[rgba(217,76,61,0.24)] bg-[rgba(217,76,61,0.12)] px-2 py-0.5 text-[10px] font-mono text-[var(--danger)]">
                  LIVE LOCKED
                </span>
              )}
            </div>

            <p className="mt-1 text-sm text-[var(--text-secondary)]">
              {botRunning
                ? 'Rules engine active, scanning the market and managing trade flow.'
                : liveBlockedByAutopilot
                  ? 'Automation is blocked until the backend explicitly allows live trading for the current autopilot mode.'
                  : 'Bot idle. Orders stay manual until you enable automation.'}
            </p>
          </div>

          <button
            onClick={() => { void handleToggle() }}
            disabled={toggleDisabled}
            aria-label="Toggle automated trading"
            className={clsx(
              'relative h-7 w-14 rounded-full border-2 transition-all duration-200 focus:outline-none',
              'disabled:cursor-not-allowed disabled:opacity-40',
              botRunning ? 'border-[var(--success)] bg-[var(--success)]' : 'border-[var(--border)] bg-[var(--bg-hover)]',
            )}
          >
            <span
              className={clsx(
                'absolute top-0.5 h-5 w-5 rounded-full bg-[var(--bg-primary)] shadow transition-transform duration-200',
                botRunning ? 'translate-x-7' : 'translate-x-0.5',
              )}
            />
          </button>
        </div>

        {hasStats && (
          <div className="flex flex-wrap items-center gap-4 border-t border-[var(--border)] px-5 py-3 text-[10px] font-mono">
            <div className="flex items-center gap-1.5">
              <span className="uppercase tracking-wider text-[var(--text-muted)]">Rules</span>
              <span className="font-semibold text-[var(--text-primary)]">{cycleStats.rulesEnabled}</span>
            </div>
            {cycleStats.symbolsScanned > 0 && (
              <div className="flex items-center gap-1.5">
                <span className="uppercase tracking-wider text-[var(--text-muted)]">Symbols</span>
                <span className="font-semibold text-[var(--text-primary)]">{cycleStats.symbolsScanned.toLocaleString()}</span>
              </div>
            )}
            <div className="flex items-center gap-1.5">
              <span className="uppercase tracking-wider text-[var(--text-muted)]">Signals</span>
              <span className={clsx('font-semibold', cycleStats.signals > 0 ? 'text-[var(--success)]' : 'text-[var(--text-muted)]')}>
                {cycleStats.signals}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="uppercase tracking-wider text-[var(--text-muted)]">Last</span>
              <span className="text-[var(--text-secondary)]">{fmtTime(cycleStats.lastRun)}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="uppercase tracking-wider text-[var(--text-muted)]">Next</span>
              <span className="text-[var(--text-secondary)]">{fmtTime(cycleStats.nextRun)}</span>
            </div>
          </div>
        )}
      </div>

      <ConfirmModal
        open={confirmOpen}
        title="Enable live automated trading"
        description={
          <>
            <p>You are about to enable automation against a live brokerage connection.</p>
            <p>Real orders may be submitted without another inline confirmation once the bot is running.</p>
          </>
        }
        confirmPhrase="ENABLE LIVE"
        confirmLabel="Enable live bot"
        destructive
        onConfirm={() => { void handleConfirmStart() }}
        onCancel={() => setConfirmOpen(false)}
      />
    </>
  )
}
