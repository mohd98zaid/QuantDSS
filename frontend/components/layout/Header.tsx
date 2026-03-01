'use client'

export function Header() {
  return (
    <header className="h-14 bg-surface-900 border-b border-gray-800 flex items-center justify-between px-6">
      {/* Left — Breadcrumb / Title */}
      <div className="flex items-center gap-3">
        <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" title="System Active"></div>
        <span className="text-sm text-gray-400">NSE</span>
      </div>

      {/* Right — Quick info */}
      <div className="flex items-center gap-6 text-sm">
        <div className="text-gray-400">
          <span className="text-gray-500">NIFTY:</span>{' '}
          <span className="font-mono-nums">—</span>
        </div>
        <div className="text-gray-400">
          <span className="text-gray-500">Risk:</span>{' '}
          <span className="text-green-400 font-medium">ACTIVE</span>
        </div>
      </div>
    </header>
  )
}
