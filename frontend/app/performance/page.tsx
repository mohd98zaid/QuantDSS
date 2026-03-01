'use client'

import { useEffect, useState } from 'react'
import { EquityCurveChart, DrawdownChart, DailyPnlChart } from '@/components/charts'

interface EquityPoint {
  date: string
  balance: number
  pnl: number
}

interface DrawdownPoint {
  date: string
  drawdown_pct: number
}

export default function PerformancePage() {
  const [equityData, setEquityData] = useState<EquityPoint[]>([])
  const [drawdownData, setDrawdownData] = useState<DrawdownPoint[]>([])
  const [days, setDays] = useState(30)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function fetchData() {
      setLoading(true)
      try {
        const token = typeof window !== 'undefined' ? localStorage.getItem('quantdss_token') : null
        const headers: Record<string, string> = { 'Content-Type': 'application/json' }
        if (token) headers['Authorization'] = `Bearer ${token}`
        const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api'

        const [eqRes, ddRes] = await Promise.all([
          fetch(`${baseUrl}/v1/performance/equity-curve?days=${days}`, { headers }),
          fetch(`${baseUrl}/v1/performance/drawdown?days=${days}`, { headers }),
        ])

        if (eqRes.ok) {
          const eq = await eqRes.json()
          setEquityData(eq.data || [])
        }
        if (ddRes.ok) {
          const dd = await ddRes.json()
          setDrawdownData(dd.data || [])
        }
      } catch (e) {
        console.error('Failed to fetch performance data', e)
      } finally {
        setLoading(false)
      }
    }
    fetchData()
  }, [days])

  // Compute KPIs from equity data
  const latestBalance = equityData.length > 0 ? equityData[equityData.length - 1].balance : 0
  const firstBalance = equityData.length > 0 ? equityData[0].balance : 0
  const totalReturn = firstBalance > 0 ? ((latestBalance - firstBalance) / firstBalance * 100) : 0
  const totalPnl = equityData.reduce((sum, d) => sum + d.pnl, 0)
  const maxDD = drawdownData.length > 0 ? Math.max(...drawdownData.map(d => d.drawdown_pct)) : 0
  const winDays = equityData.filter(d => d.pnl > 0).length
  const totalDays = equityData.filter(d => d.pnl !== 0).length
  const winRate = totalDays > 0 ? (winDays / totalDays * 100) : 0

  const kpis = [
    { label: 'Total Return', value: `${totalReturn >= 0 ? '+' : ''}${totalReturn.toFixed(1)}%`, color: totalReturn >= 0 ? 'text-emerald-400' : 'text-red-400' },
    { label: 'Net P&L', value: `₹${totalPnl.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`, color: totalPnl >= 0 ? 'text-emerald-400' : 'text-red-400' },
    { label: 'Max Drawdown', value: `${maxDD.toFixed(1)}%`, color: maxDD > 5 ? 'text-red-400' : 'text-yellow-400' },
    { label: 'Win Rate', value: `${winRate.toFixed(0)}%`, color: winRate >= 50 ? 'text-emerald-400' : 'text-gray-400' },
    { label: 'Balance', value: `₹${latestBalance.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`, color: 'text-blue-400' },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Performance</h1>
        <p className="text-gray-400 mt-1">Equity curve, drawdown, and daily P&amp;L</p>
      </div>

      {/* KPI Row */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        {kpis.map((card) => (
          <div key={card.label} className="bg-surface-900 border border-gray-800 rounded-xl p-4">
            <p className="text-xs text-gray-500">{card.label}</p>
            <p className={`text-xl font-bold font-mono-nums mt-1 ${card.color}`}>{card.value}</p>
          </div>
        ))}
      </div>

      {/* Charts Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Equity Curve */}
        <div className="bg-surface-900 border border-gray-800 rounded-xl p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold">Equity Curve</h2>
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="bg-surface-800 border border-gray-700 rounded-lg px-2 py-1 text-xs text-gray-400"
            >
              <option value={30}>30 Days</option>
              <option value={90}>90 Days</option>
              <option value={180}>6 Months</option>
              <option value={365}>1 Year</option>
            </select>
          </div>
          {loading ? (
            <div className="flex items-center justify-center h-48 text-gray-500">Loading...</div>
          ) : (
            <EquityCurveChart data={equityData} />
          )}
        </div>

        {/* Drawdown */}
        <div className="bg-surface-900 border border-gray-800 rounded-xl p-6">
          <h2 className="text-lg font-semibold mb-4">Drawdown from Peak</h2>
          {loading ? (
            <div className="flex items-center justify-center h-48 text-gray-500">Loading...</div>
          ) : (
            <DrawdownChart data={drawdownData} />
          )}
        </div>
      </div>

      {/* Daily P&L */}
      <div className="bg-surface-900 border border-gray-800 rounded-xl p-6">
        <h2 className="text-lg font-semibold mb-4">Daily P&amp;L</h2>
        {loading ? (
          <div className="flex items-center justify-center h-48 text-gray-500">Loading...</div>
        ) : (
          <DailyPnlChart data={equityData} />
        )}
      </div>

      {/* Strategy Breakdown */}
      <div className="bg-surface-900 border border-gray-800 rounded-xl p-6">
        <h2 className="text-lg font-semibold mb-4">Per-Strategy Breakdown</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-gray-400 text-left">
                <th className="p-3">Strategy</th>
                <th className="p-3">Trades</th>
                <th className="p-3">Win Rate</th>
                <th className="p-3">Net P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={4} className="p-8 text-center text-gray-500">
                  Strategy performance data will populate with trading history
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
