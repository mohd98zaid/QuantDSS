'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

const NAV_ITEMS = [
  { href: '/',            label: 'Dashboard',   icon: '📊' },
  { href: '/signals',     label: 'Signals',     icon: '🔔' },
  { href: '/journal',     label: 'Journal',     icon: '📝' },
  { href: '/backtest',    label: 'Backtest',    icon: '🧪' },
  { href: '/performance', label: 'Performance', icon: '📈' },
  { href: '/settings',    label: 'Settings',    icon: '⚙️' },
]

export function Sidebar() {
  const pathname = usePathname()

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
          <span className="w-2 h-2 rounded-full bg-gray-600"></span>
          <span>Market Closed</span>
        </div>
      </div>
    </aside>
  )
}
