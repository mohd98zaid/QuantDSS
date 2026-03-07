'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'

const NAV_ITEMS = [
  { href: '/',             label: 'Dashboard',    icon: '📊' },
  { href: '/scanner',      label: 'Scanner',      icon: '🔍' },
  { href: '/signals',      label: 'Signals',      icon: '🔔' },
  { href: '/paper',        label: 'Paper Trade',  icon: '🕹️' },
  { href: '/auto-trader',  label: 'Auto Trader',  icon: '🤖' },
  { href: '/market',       label: 'Market Data',  icon: '🕯️' },
  { href: '/journal',      label: 'Journal',      icon: '📝' },
  { href: '/backtest',     label: 'Backtest',     icon: '🧪' },
  { href: '/performance',  label: 'Performance',  icon: '📈' },
  { href: '/settings',     label: 'Settings',     icon: '⚙️' },
]

export function Sidebar() {
  const pathname = usePathname()
  const [marketOpen, setMarketOpen] = useState<boolean | null>(null)
  const [marketTime, setMarketTime] = useState<string>('')

  useEffect(() => {
    async function fetchMarket() {
      try {
        const res = await fetch('/api/v1/health/market')
        if (!res.ok) throw new Error('fetch failed')
        const data = await res.json()
        setMarketOpen(data.is_open)
        setMarketTime(data.current_time_ist)
      } catch {
        setMarketOpen(false)
      }
    }
    fetchMarket()
    const interval = setInterval(fetchMarket, 60_000) // refresh every minute
    return () => clearInterval(interval)
  }, [pathname])

  return (
    <aside className="w-64 bg-surface-900 border-r border-gray-800 flex flex-col">
      {/* Logo */}
      <div className="p-6 border-b border-gray-800">
        <h1 className="text-lg font-bold text-blue-400">QuantDSS</h1>
        <p className="text-xs text-gray-500 mt-0.5">Decision Support System</p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-4 space-y-1">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname === item.href
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                isActive
                  ? 'bg-blue-500/10 text-blue-400 font-medium'
                  : 'text-gray-400 hover:text-white hover:bg-surface-800'
              }`}
            >
              <span className="text-lg">{item.icon}</span>
              <span>{item.label}</span>
            </Link>
          )
        })}
      </nav>

      {/* Market status */}
      <div className="p-4 border-t border-gray-800">
        <div className="flex items-center gap-2 text-xs text-gray-500">
          {marketOpen === null ? (
            // Loading state
            <span className="w-2 h-2 rounded-full bg-gray-600 animate-pulse" />
          ) : marketOpen ? (
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          ) : (
            <span className="w-2 h-2 rounded-full bg-red-500" />
          )}
          <span>
            {marketOpen === null
              ? 'Checking market...'
              : marketOpen
              ? `Market Open${marketTime ? ` · ${marketTime}` : ''}`
              : 'Market Closed'}
          </span>
        </div>
      </div>
    </aside>
  )
}
