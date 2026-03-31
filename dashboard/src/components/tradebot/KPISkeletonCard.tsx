export function KPISkeletonCard() {
  return (
    <div className="bg-zinc-900/80 border border-zinc-800 rounded-2xl p-5 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 rounded-lg bg-zinc-800/40 animate-pulse" />
        <div className="h-2.5 w-24 rounded-lg bg-zinc-800/40 animate-pulse" />
      </div>
      <div className="h-7 w-36 rounded-xl bg-zinc-800/30 animate-pulse" />
    </div>
  )
}
