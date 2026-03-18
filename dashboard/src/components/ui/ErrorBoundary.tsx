import React from 'react'

interface Props {
  children: React.ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
  stackOpen: boolean
}

export default class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null, stackOpen: false }
  }

  static getDerivedStateFromError(error: Error): Omit<State, 'stackOpen'> {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      const { error, stackOpen } = this.state

      return (
        <div className="flex flex-col items-center justify-center min-h-[16rem] p-6">
          {/* Gradient border card — wraps in a 1px gradient shell */}
          <div
            className="rounded-2xl p-px max-w-lg w-full"
            style={{
              background: 'linear-gradient(135deg, rgba(239,68,68,0.5) 0%, rgba(245,158,11,0.35) 50%, rgba(239,68,68,0.15) 100%)',
            }}
          >
            <div className="card rounded-2xl p-7 flex flex-col gap-5">

              {/* Header row: icon + title */}
              <div className="flex items-start gap-4">
                {/* Warning triangle SVG */}
                <div
                  className="shrink-0 w-10 h-10 rounded-xl flex items-center justify-center"
                  style={{ background: 'rgba(239,68,68,0.12)' }}
                >
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    className="w-5 h-5"
                    aria-hidden="true"
                  >
                    <path
                      d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"
                      stroke="#ef4444"
                      strokeWidth="1.75"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                    <line
                      x1="12" y1="9" x2="12" y2="13"
                      stroke="#ef4444"
                      strokeWidth="1.75"
                      strokeLinecap="round"
                    />
                    <line
                      x1="12" y1="17" x2="12.01" y2="17"
                      stroke="#ef4444"
                      strokeWidth="2"
                      strokeLinecap="round"
                    />
                  </svg>
                </div>

                <div className="flex-1 min-w-0">
                  <p className="text-sm font-sans font-semibold text-red-400 leading-tight mb-1">
                    Something went wrong
                  </p>
                  <p className="text-xs font-sans text-zinc-400 leading-relaxed break-words">
                    {error?.message || 'An unexpected error occurred in this component tree.'}
                  </p>
                </div>
              </div>

              {/* Stack trace collapsible */}
              {error?.stack && (
                <div className="rounded-xl overflow-hidden border border-zinc-800">
                  <button
                    type="button"
                    onClick={() => this.setState((s) => ({ stackOpen: !s.stackOpen }))}
                    className="w-full flex items-center justify-between px-4 py-2.5 text-left hover:bg-zinc-900 transition-colors"
                  >
                    <span className="text-[11px] font-mono text-zinc-500 uppercase tracking-wider">
                      Stack trace
                    </span>
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      className={[
                        'w-3.5 h-3.5 text-zinc-500 transition-transform duration-200',
                        stackOpen ? 'rotate-180' : '',
                      ].join(' ')}
                      aria-hidden="true"
                    >
                      <path
                        d="M6 9l6 6 6-6"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </button>

                  {stackOpen && (
                    <pre
                      className={[
                        'px-4 py-3 text-[10px] font-mono leading-relaxed',
                        'text-zinc-500 overflow-x-auto whitespace-pre-wrap break-all',
                        'border-t border-zinc-800',
                        'max-h-40',
                      ].join(' ')}
                      style={{ background: 'rgba(0,0,0,0.25)' }}
                    >
                      {error.stack}
                    </pre>
                  )}
                </div>
              )}

              {/* Action row */}
              <div className="flex items-center gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => window.location.reload()}
                  className={[
                    'flex items-center gap-2',
                    'px-4 py-2 rounded-xl',
                    'text-xs font-sans font-medium',
                    'text-red-400 border border-red-300',
                    'hover:bg-red-500/10 active:bg-red-500/10',
                    'transition-colors duration-150',
                  ].join(' ')}
                >
                  {/* Refresh / retry icon */}
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    className="w-3.5 h-3.5"
                    aria-hidden="true"
                  >
                    <path
                      d="M1 4v6h6"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                    <path
                      d="M3.51 15a9 9 0 1 0 .49-4.5"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                  Retry
                </button>

                <button
                  type="button"
                  onClick={() => this.setState({ hasError: false, error: null, stackOpen: false })}
                  className={[
                    'px-4 py-2 rounded-xl',
                    'text-xs font-sans font-medium',
                    'text-zinc-400 border border-zinc-800',
                    'hover:bg-zinc-800/60 active:bg-zinc-800',
                    'transition-colors duration-150',
                  ].join(' ')}
                >
                  Dismiss
                </button>
              </div>
            </div>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
