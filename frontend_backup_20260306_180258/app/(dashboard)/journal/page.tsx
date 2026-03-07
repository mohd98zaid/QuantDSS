'use client'

import { useEffect, useState } from 'react'
import { getTrades } from '@/lib/api'

interface Trade {
  id: number
  symbol?: string
  signal_type?: string
  direction?: string
  entry_price?: number
  exit_price?: number
  quantity?: number
  net_pnl?: number
  gross_pnl?: number
  notes?: string
  entry_time?: string
  exit_time?: string
  created_at?: string
}

const PERIODS = [
  { label: 'Today', value: 'today' },
  { label: 'This Week', value: 'week' },
  { label: 'This Month', value: 'month' },
  { label: 'All', value: 'all' },
]

function periodToParams(period: string): Record<string, string> {
  const now = new Date()
  if (period === 'today') {
    const d = now.toISOString().slice(0, 10)
    return { from_date: d, to_date: d }
  }
  if (period === 'week') {
    const start = new Date(now)
    start.setDate(now.getDate() - now.getDay())
    return { from_date: start.toISOString().slice(0, 10) }
  }
  if (period === 'month') {
    return { from_date: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01` }
  }
  return {}
}

export default function JournalPage() {
  const [period, setPeriod] = useState('today')
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    loadTrades(period)
  }, [period])

  async function loadTrades(p: string) {
    setLoading(true)
    setError(null)
    try {
      const params = periodToParams(p)
      const data = await getTrades(params)
      // Support both {trades: [...]} and plain array responses
      setTrades(data?.trades ?? data ?? [])
    } catch (e: any) {
      setError(e.message || 'Failed to load trades')
      setTrades([])
    } finally {
      setLoading(false)
    }
  }

  // KPIs
  const totalPnl = trades.reduce((s, t) => s + (t.net_pnl ?? t.gross_pnl ?? 0), 0)
  const winners = trades.filter(t => (t.net_pnl ?? t.gross_pnl ?? 0) > 0)
  const losers = trades.filter(t => (t.net_pnl ?? t.gross_pnl ?? 0) < 0)
  const winRate = trades.length > 0 ? ((winners.length / trades.length) * 100).toFixed(0) : '—'
  const avgPnl = trades.length > 0 ? (totalPnl / trades.length).toFixed(2) : '0.00'

  const fmtDate = (s?: string) => {
    if (!s) return '—'
    try {
      return new Date(s).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
    } catch { return s }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Trade Journal</h1>
          <p className="text-gray-400 mt-1">All logged trade outcomes</p>
        </div>
      </div>

      {/* Period Selector */}
      <div className="flex gap-2">
        {PERIODS.map((p) => (
          <button
            key={p.value}
            onClick={() => setPeriod(p.value)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              period === p.value
                ? 'bg-blue-600 text-white'
                : 'bg-gray-900 text-gray-400 hover:text-white border border-gray-800'
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          {
            label: 'Net P&L',
            value: totalPnl >= 0 ? `+₹${totalPnl.toFixed(2)}` : `-₹${Math.abs(totalPnl).toFixed(2)}`,
            color: totalPnl >= 0 ? 'text-emerald-400' : 'text-red-400',
          },
          {
            label: 'Win Rate',
            value: trades.length > 0 ? `${winRate}%` : '—',
            color: parseInt(winRate) >= 50 ? 'text-emerald-400' : 'text-gray-400',
          },
          {
            label: 'Total Trades',
            value: trades.length.toString(),
            color: 'text-blue-400',
          },
          {
            label: 'Avg P&L/Trade',
            value: trades.length > 0 ? `₹${avgPnl}` : '—',
            color: parseFloat(avgPnl) >= 0 ? 'text-emerald-400' : 'text-red-400',
          },
        ].map((card) => (
          <div key={card.label} className="bg-gray-900/80 border border-gray-800 rounded-xl p-4">
            <p className="text-xs text-gray-500 uppercase tracking-wide">{card.label}</p>
            <p className={`text-xl font-bold font-mono-nums mt-2 ${card.color}`}>{card.value}</p>
          </div>
        ))}
      </div>

      {/* Win/Loss mini bar */}
      {trades.length > 0 && (
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span className="text-emerald-400">{winners.length}W</span>
          <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-emerald-500 rounded-full"
              style={{ width: `${(winners.length / trades.length) * 100}%` }}
            />
          </div>
          <span className="text-red-400">{losers.length}L</span>
        </div>
      )}

      {/* Trade Table */}
      <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-gray-400 text-left">
              <th className="p-4 text-xs uppercase tracking-wide">Date</th>
              <th className="p-4 text-xs uppercase tracking-wide">Symbol</th>
              <th className="p-4 text-xs uppercase tracking-wide">Direction</th>
              <th className="p-4 text-xs uppercase tracking-wide">Entry</th>
              <th className="p-4 text-xs uppercase tracking-wide">Exit</th>
              <th className="p-4 text-xs uppercase tracking-wide">Qty</th>
              <th className="p-4 text-xs uppercase tracking-wide">Net P&L</th>
              <th className="p-4 text-xs uppercase tracking-wide hidden md:table-cell">Notes</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={8} className="p-10 text-center text-gray-500">
                  <div className="flex justify-center gap-2 items-center">
                    <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                    Loading…
                  </div>
                </td>
              </tr>
            ) : error ? (
              <tr>
                <td colSpan={8} className="p-8 text-center text-red-400 text-sm">⚠️ {error}</td>
              </tr>
            ) : trades.length === 0 ? (
              <tr>
                <td colSpan={8} className="p-12 text-center text-gray-500">
                  <span className="text-3xl block mb-3">📝</span>
                  <p className="text-sm">No trades logged for this period.</p>
                  <p className="text-xs text-gray-600 mt-1">Trades are recorded when you log them via the API or journal entries appear after signal execution.</p>
                </td>
              </tr>
            ) : (
              trades.map((t) => {
                const pnl = t.net_pnl ?? t.gross_pnl ?? 0
                const pnlColor = pnl >= 0 ? 'text-emerald-400' : 'text-red-400'
                const dir = t.signal_type || t.direction || '—'
                return (
                  <tr key={t.id} className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors">
                    <td className="p-4 text-gray-400 text-xs font-mono">{fmtDate(t.entry_time || t.created_at)}</td>
                    <td className="p-4 font-semibold text-white">{t.symbol || '—'}</td>
                    <td className={`p-4 font-bold text-xs ${dir === 'BUY' ? 'text-emerald-400' : dir === 'SELL' ? 'text-red-400' : 'text-gray-400'}`}>
                      {dir}
                    </td>
                    <td className="p-4 font-mono text-gray-300">₹{t.entry_price?.toFixed(2) ?? '—'}</td>
                    <td className="p-4 font-mono text-gray-300">₹{t.exit_price?.toFixed(2) ?? '—'}</td>
                    <td className="p-4 font-mono text-gray-400">{t.quantity ?? '—'}</td>
                    <td className={`p-4 font-mono font-bold ${pnlColor}`}>
                      {pnl >= 0 ? '+' : ''}₹{pnl.toFixed(2)}
                    </td>
                    <td className="p-4 text-gray-500 text-xs hidden md:table-cell max-w-xs truncate">
                      {t.notes || '—'}
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
