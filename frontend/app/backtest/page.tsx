'use client'

import { useState } from 'react'
import { EquityCurveChart } from '@/components/charts'

interface BacktestMetrics {
  total_return_pct: number
  win_rate: number
  sharpe_ratio: number
  max_drawdown_pct: number
  total_trades: number
  profit_factor: number
  avg_win: number
  avg_loss: number
  initial_capital: number
  final_capital: number
}

interface BacktestTradeData {
  signal_type: string
  entry_time: string
  exit_time: string
  entry_price: number
  exit_price: number
  quantity: number
  pnl: number
  exit_reason: string
}

export default function BacktestPage() {
  const [isRunning, setIsRunning] = useState(false)
  const [metrics, setMetrics] = useState<BacktestMetrics | null>(null)
  const [trades, setTrades] = useState<BacktestTradeData[]>([])
  const [equityCurve, setEquityCurve] = useState<{ date: string; balance: number; pnl: number }[]>([])
  const [error, setError] = useState('')

  // Config state
  const [strategy, setStrategy] = useState('1')
  const [symbol, setSymbol] = useState('1')
  const [timeframe, setTimeframe] = useState('1d')
  const [capital, setCapital] = useState(100000)

  async function runBacktest() {
    setIsRunning(true)
    setError('')
    setMetrics(null)
    setTrades([])
    setEquityCurve([])

    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('quantdss_token') : null
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (token) headers['Authorization'] = `Bearer ${token}`
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api'

      const res = await fetch(`${baseUrl}/v1/backtest/run`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          strategy_id: Number(strategy),
          symbol_id: Number(symbol),
          timeframe,
          initial_capital: capital,
        }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Backtest failed' }))
        throw new Error(err.detail)
      }

      const data = await res.json()
      setMetrics({
        total_return_pct: data.total_return_pct,
        win_rate: data.win_rate,
        sharpe_ratio: data.sharpe_ratio,
        max_drawdown_pct: data.max_drawdown_pct,
        total_trades: data.total_trades,
        profit_factor: data.profit_factor || 0,
        avg_win: data.avg_win || 0,
        avg_loss: data.avg_loss || 0,
        initial_capital: data.initial_capital,
        final_capital: data.final_capital,
      })

      // Build equity curve from trade data for the chart
      if (data.id) {
        const detailRes = await fetch(`${baseUrl}/v1/backtest/runs/${data.id}`, { headers })
        if (detailRes.ok) {
          const detail = await detailRes.json()
          setTrades(detail.trades || [])

          // Build equity curve from trades
          let balance = data.initial_capital
          const curve = [{ date: 'Start', balance, pnl: 0 }]
          for (const t of detail.trades || []) {
            balance += t.pnl
            curve.push({
              date: t.exit_time?.slice(0, 10) || '',
              balance: Math.round(balance),
              pnl: Math.round(t.pnl),
            })
          }
          setEquityCurve(curve)
        }
      }
    } catch (e: any) {
      setError(e.message || 'Backtest failed')
    } finally {
      setIsRunning(false)
    }
  }

  const metricCards = metrics ? [
    { label: 'Return', value: `${metrics.total_return_pct >= 0 ? '+' : ''}${metrics.total_return_pct}%`, color: metrics.total_return_pct >= 0 ? 'text-emerald-400' : 'text-red-400' },
    { label: 'Win Rate', value: `${metrics.win_rate}%`, color: metrics.win_rate >= 50 ? 'text-emerald-400' : 'text-yellow-400' },
    { label: 'Sharpe', value: `${metrics.sharpe_ratio}`, color: metrics.sharpe_ratio >= 1 ? 'text-emerald-400' : 'text-gray-400' },
    { label: 'Max DD', value: `${metrics.max_drawdown_pct}%`, color: metrics.max_drawdown_pct > 10 ? 'text-red-400' : 'text-yellow-400' },
    { label: 'Total Trades', value: `${metrics.total_trades}`, color: 'text-blue-400' },
    { label: 'Profit Factor', value: `${metrics.profit_factor}`, color: metrics.profit_factor >= 1.5 ? 'text-emerald-400' : 'text-gray-400' },
    { label: 'Avg Win', value: `₹${metrics.avg_win.toLocaleString('en-IN')}`, color: 'text-emerald-400' },
    { label: 'Avg Loss', value: `₹${metrics.avg_loss.toLocaleString('en-IN')}`, color: 'text-red-400' },
  ] : [
    { label: 'Return', value: '—', color: 'text-gray-500' },
    { label: 'Win Rate', value: '—', color: 'text-gray-500' },
    { label: 'Sharpe', value: '—', color: 'text-gray-500' },
    { label: 'Max DD', value: '—', color: 'text-gray-500' },
    { label: 'Total Trades', value: '—', color: 'text-gray-500' },
    { label: 'Profit Factor', value: '—', color: 'text-gray-500' },
    { label: 'Avg Win', value: '—', color: 'text-gray-500' },
    { label: 'Avg Loss', value: '—', color: 'text-gray-500' },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Backtesting</h1>
        <p className="text-gray-400 mt-1">Test strategies against historical data</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Config Panel */}
        <div className="bg-surface-900 border border-gray-800 rounded-xl p-6">
          <h2 className="text-lg font-semibold mb-4">Configuration</h2>

          <div className="space-y-4">
            <div>
              <label className="text-sm text-gray-400 block mb-1">Strategy</label>
              <select value={strategy} onChange={(e) => setStrategy(e.target.value)}
                className="w-full bg-surface-800 border border-gray-700 rounded-lg px-3 py-2 text-sm">
                <option value="1">EMA Crossover</option>
                <option value="2">RSI Mean Reversion</option>
              </select>
            </div>

            <div>
              <label className="text-sm text-gray-400 block mb-1">Symbol</label>
              <select value={symbol} onChange={(e) => setSymbol(e.target.value)}
                className="w-full bg-surface-800 border border-gray-700 rounded-lg px-3 py-2 text-sm">
                <option value="1">RELIANCE</option>
                <option value="2">TCS</option>
                <option value="3">INFY</option>
                <option value="4">HDFCBANK</option>
                <option value="5">ICICIBANK</option>
                <option value="6">SBIN</option>
              </select>
            </div>

            <div>
              <label className="text-sm text-gray-400 block mb-1">Timeframe</label>
              <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}
                className="w-full bg-surface-800 border border-gray-700 rounded-lg px-3 py-2 text-sm">
                <option value="1d">Daily (1D)</option>
                <option value="1h">Hourly (1H)</option>
              </select>
            </div>

            <div>
              <label className="text-sm text-gray-400 block mb-1">Initial Capital (₹)</label>
              <input type="number" value={capital} onChange={(e) => setCapital(Number(e.target.value))}
                className="w-full bg-surface-800 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono-nums" />
            </div>

            <button onClick={runBacktest} disabled={isRunning}
              className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white py-2.5 rounded-lg font-medium text-sm transition-colors">
              {isRunning ? '⏳ Running...' : '🚀 Run Backtest'}
            </button>

            {error && <p className="text-red-400 text-sm text-center">{error}</p>}
          </div>
        </div>

        {/* Results Panel */}
        <div className="lg:col-span-2 space-y-6">
          {/* Metrics Grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {metricCards.map((m) => (
              <div key={m.label} className="bg-surface-900 border border-gray-800 rounded-lg p-3">
                <p className="text-xs text-gray-500">{m.label}</p>
                <p className={`text-lg font-bold font-mono-nums ${m.color}`}>{m.value}</p>
              </div>
            ))}
          </div>

          {/* Equity Curve Chart */}
          <div className="bg-surface-900 border border-gray-800 rounded-xl p-6">
            <h3 className="text-sm font-medium text-gray-400 mb-4">Equity Curve</h3>
            {equityCurve.length > 0 ? (
              <EquityCurveChart data={equityCurve} initialBalance={capital} />
            ) : (
              <div className="flex items-center justify-center h-48 text-gray-500">
                <span className="text-4xl mr-3">📈</span>
                <span>Run a backtest to see the equity curve</span>
              </div>
            )}
          </div>

          {/* Trade List */}
          <div className="bg-surface-900 border border-gray-800 rounded-xl p-6">
            <h3 className="text-sm font-medium text-gray-400 mb-4">
              Trades {trades.length > 0 && `(${trades.length})`}
            </h3>
            {trades.length > 0 ? (
              <div className="overflow-x-auto max-h-64 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-surface-900">
                    <tr className="border-b border-gray-800 text-gray-400">
                      <th className="p-2 text-left">Type</th>
                      <th className="p-2 text-left">Entry</th>
                      <th className="p-2 text-left">Exit</th>
                      <th className="p-2 text-left">Qty</th>
                      <th className="p-2 text-left">P&amp;L</th>
                      <th className="p-2 text-left">Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.map((t, i) => (
                      <tr key={i} className="border-b border-gray-800/50 hover:bg-surface-800/50">
                        <td className={`p-2 font-medium ${t.signal_type === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>
                          {t.signal_type}
                        </td>
                        <td className="p-2 text-gray-300 font-mono-nums">₹{t.entry_price?.toFixed(2)}</td>
                        <td className="p-2 text-gray-300 font-mono-nums">₹{t.exit_price?.toFixed(2)}</td>
                        <td className="p-2 text-gray-400">{t.quantity}</td>
                        <td className={`p-2 font-mono-nums font-medium ${t.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {t.pnl >= 0 ? '+' : ''}₹{t.pnl?.toFixed(0)}
                        </td>
                        <td className="p-2 text-gray-500">{t.exit_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="flex items-center justify-center h-16 text-gray-500 text-sm">
                No backtest results yet
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
