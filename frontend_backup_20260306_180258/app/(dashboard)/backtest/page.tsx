'use client'

import { useEffect, useState } from 'react'
import { EquityCurveChart } from '@/components/charts'
import { getSymbols, getStrategies } from '@/lib/api'

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

  // Dynamic symbol/strategy lists
  const [symbols, setSymbols] = useState<any[]>([])
  const [strategies, setStrategies] = useState<any[]>([])
  const [loadingMeta, setLoadingMeta] = useState(true)

  // Config state
  const [strategy, setStrategy] = useState('')
  const [symbol, setSymbol] = useState('')
  const [timeframe, setTimeframe] = useState('1day')
  const [capital, setCapital] = useState(100000)

  useEffect(() => {
    async function loadMeta() {
      setLoadingMeta(true)
      try {
        const [syms, strats] = await Promise.all([getSymbols(), getStrategies()])
        setSymbols(syms || [])
        setStrategies(strats || [])
        if (syms?.length > 0) setSymbol(String(syms[0].id))
        if (strats?.length > 0) setStrategy(String(strats[0].id))
      } catch {
        // Leave empty — show fallback message
      } finally {
        setLoadingMeta(false)
      }
    }
    loadMeta()
  }, [])

  async function runBacktest() {
    if (!strategy || !symbol) {
      setError('Select a strategy and symbol first.')
      return
    }
    setIsRunning(true)
    setError('')
    setMetrics(null)
    setTrades([])
    setEquityCurve([])

    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('quantdss_token') : null
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (token) headers['Authorization'] = `Bearer ${token}`
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || '/api'

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

      if (data.id) {
        const detailRes = await fetch(`${baseUrl}/v1/backtest/runs/${data.id}`, { headers })
        if (detailRes.ok) {
          const detail = await detailRes.json()
          setTrades(detail.trades || [])
          let balance = data.initial_capital
          const curve = [{ date: 'Start', balance, pnl: 0 }]
          for (const t of detail.trades || []) {
            balance += t.pnl
            curve.push({ date: t.exit_time?.slice(0, 10) || '', balance: Math.round(balance), pnl: Math.round(t.pnl) })
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
    { label: 'Trades', value: `${metrics.total_trades}`, color: 'text-blue-400' },
    { label: 'Profit Factor', value: `${metrics.profit_factor}`, color: metrics.profit_factor >= 1.5 ? 'text-emerald-400' : 'text-gray-400' },
    { label: 'Avg Win', value: `₹${metrics.avg_win.toLocaleString('en-IN')}`, color: 'text-emerald-400' },
    { label: 'Avg Loss', value: `₹${metrics.avg_loss.toLocaleString('en-IN')}`, color: 'text-red-400' },
  ] : [
    { label: 'Return', value: '—', color: 'text-gray-600' },
    { label: 'Win Rate', value: '—', color: 'text-gray-600' },
    { label: 'Sharpe', value: '—', color: 'text-gray-600' },
    { label: 'Max DD', value: '—', color: 'text-gray-600' },
    { label: 'Trades', value: '—', color: 'text-gray-600' },
    { label: 'Profit Factor', value: '—', color: 'text-gray-600' },
    { label: 'Avg Win', value: '—', color: 'text-gray-600' },
    { label: 'Avg Loss', value: '—', color: 'text-gray-600' },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Backtesting</h1>
        <p className="text-gray-400 mt-1">Test strategies against historical OHLCV data</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Config Panel */}
        <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-6">
          <h2 className="text-lg font-semibold mb-5">Configuration</h2>

          {loadingMeta ? (
            <div className="flex items-center gap-2 text-gray-500 text-sm">
              <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
              Loading symbols & strategies…
            </div>
          ) : (
            <div className="space-y-4">
              <div>
                <label className="text-xs text-gray-500 uppercase tracking-wide block mb-1.5">Strategy</label>
                {strategies.length > 0 ? (
                  <select
                    value={strategy}
                    onChange={(e) => setStrategy(e.target.value)}
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
                  >
                    {strategies.map((s) => (
                      <option key={s.id} value={s.id}>{s.name || `Strategy #${s.id}`}</option>
                    ))}
                  </select>
                ) : (
                  <p className="text-xs text-yellow-400 px-2 py-1.5 bg-yellow-950/30 border border-yellow-900/50 rounded-lg">
                    No strategies found. Strategies are seeded at startup.
                  </p>
                )}
              </div>

              <div>
                <label className="text-xs text-gray-500 uppercase tracking-wide block mb-1.5">Symbol</label>
                {symbols.length > 0 ? (
                  <select
                    value={symbol}
                    onChange={(e) => setSymbol(e.target.value)}
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
                  >
                    {symbols.map((s) => (
                      <option key={s.id} value={s.id}>{s.trading_symbol} ({s.exchange})</option>
                    ))}
                  </select>
                ) : (
                  <p className="text-xs text-yellow-400 px-2 py-1.5 bg-yellow-950/30 border border-yellow-900/50 rounded-lg">
                    No symbols. Add symbols in <strong>Settings</strong> first.
                  </p>
                )}
              </div>

              <div>
                <label className="text-xs text-gray-500 uppercase tracking-wide block mb-1.5">Timeframe</label>
                <select
                  value={timeframe}
                  onChange={(e) => setTimeframe(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
                >
                  <option value="1day">Daily (1D)</option>
                  <option value="1hour">Hourly (1H)</option>
                  <option value="30min">30 min</option>
                  <option value="15min">15 min</option>
                  <option value="5min">5 min</option>
                </select>
              </div>

              <div>
                <label className="text-xs text-gray-500 uppercase tracking-wide block mb-1.5">Initial Capital (₹)</label>
                <input
                  type="number"
                  value={capital}
                  onChange={(e) => setCapital(Number(e.target.value))}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono-nums focus:outline-none focus:border-blue-500"
                />
              </div>

              <button
                onClick={runBacktest}
                disabled={isRunning || loadingMeta || !symbol || !strategy}
                className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white py-2.5 rounded-lg font-medium text-sm transition-colors"
              >
                {isRunning ? '⏳ Running…' : '🚀 Run Backtest'}
              </button>

              {error && (
                <p className="text-red-400 text-xs text-center px-2 py-2 bg-red-950/30 border border-red-900/50 rounded-lg">
                  ⚠️ {error}
                </p>
              )}
            </div>
          )}
        </div>

        {/* Results Panel */}
        <div className="lg:col-span-2 space-y-5">
          {/* Metrics Grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {metricCards.map((m) => (
              <div key={m.label} className="bg-gray-900/80 border border-gray-800 rounded-xl p-4">
                <p className="text-xs text-gray-500 uppercase tracking-wide">{m.label}</p>
                <p className={`text-xl font-bold font-mono-nums mt-2 ${m.color}`}>{m.value}</p>
              </div>
            ))}
          </div>

          {/* Equity Curve */}
          <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-5">
            <h3 className="text-sm font-semibold text-gray-300 mb-4">Equity Curve</h3>
            {equityCurve.length > 0 ? (
              <EquityCurveChart data={equityCurve} initialBalance={capital} />
            ) : (
              <div className="flex flex-col items-center justify-center h-48 text-gray-600 gap-2">
                <span className="text-4xl">📈</span>
                <span className="text-sm">Run a backtest to see the equity curve</span>
              </div>
            )}
          </div>

          {/* Trade List */}
          <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-5">
            <h3 className="text-sm font-semibold text-gray-300 mb-4">
              Trades {trades.length > 0 && `(${trades.length})`}
            </h3>
            {trades.length > 0 ? (
              <div className="overflow-x-auto max-h-64 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-gray-900">
                    <tr className="border-b border-gray-800 text-gray-400">
                      <th className="p-2 text-left">Type</th>
                      <th className="p-2 text-left">Entry Price</th>
                      <th className="p-2 text-left">Exit Price</th>
                      <th className="p-2 text-left">Qty</th>
                      <th className="p-2 text-left">P&L</th>
                      <th className="p-2 text-left">Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.map((t, i) => (
                      <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                        <td className={`p-2 font-bold ${t.signal_type === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>
                          {t.signal_type}
                        </td>
                        <td className="p-2 font-mono text-gray-300">₹{t.entry_price?.toFixed(2)}</td>
                        <td className="p-2 font-mono text-gray-300">₹{t.exit_price?.toFixed(2)}</td>
                        <td className="p-2 text-gray-400">{t.quantity}</td>
                        <td className={`p-2 font-mono font-bold ${t.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {t.pnl >= 0 ? '+' : ''}₹{t.pnl?.toFixed(0)}
                        </td>
                        <td className="p-2 text-gray-500">{t.exit_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="flex items-center justify-center h-16 text-gray-600 text-sm">
                No backtest results yet
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
