'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, TrendingUp, TrendingDown, Activity, BarChart2, Download, CheckCircle } from 'lucide-react'
import { getCandles, getSymbols, seedCandles, seedIntraday } from '@/lib/api'
import { CandlestickChart, OHLCCandle } from '@/components/CandlestickChart'

const TIMEFRAMES = [
  { label: '1m',  value: '1min' },
  { label: '5m',  value: '5min' },
  { label: '15m', value: '15min' },
  { label: '30m', value: '30min' },
  { label: '1h',  value: '1hour' },
  { label: '1D',  value: '1day' },
]

const LIMITS: Record<string, number> = {
  '1min':  120,
  '5min':   80,
  '15min':  60,
  '30min':  50,
  '1hour':  60,
  '1day':   90,
}

interface SymbolItem {
  id: number
  trading_symbol: string
}

function StatCard({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string
}) {
  return (
    <div className="bg-gray-900/60 border border-gray-800 rounded-xl p-4 flex flex-col gap-1">
      <span className="text-xs text-gray-500 uppercase tracking-wide">{label}</span>
      <span className={`text-xl font-bold font-mono ${color ?? 'text-white'}`}>{value}</span>
      {sub && <span className="text-xs text-gray-600">{sub}</span>}
    </div>
  )
}

export default function MarketPage() {
  const [symbols, setSymbols] = useState<SymbolItem[]>([])
  const [selectedSymbol, setSelectedSymbol] = useState<string>('')
  const [timeframe, setTimeframe] = useState('5min')
  const [candles, setCandles] = useState<OHLCCandle[]>([])
  const [loading, setLoading] = useState(false)
  const [seeding, setSeeding] = useState(false)
  const [seedingIntraday, setSeedingIntraday] = useState(false)
  const [seedResult, setSeedResult] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [error, setError] = useState<string | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Load symbols
  useEffect(() => {
    getSymbols()
      .then((list: SymbolItem[]) => {
        setSymbols(list)
        if (list.length > 0) setSelectedSymbol(list[0].trading_symbol)
      })
      .catch(() => setError('Failed to load symbols. Make sure you are logged in.'))
  }, [])

  const fetchCandles = useCallback(async () => {
    if (!selectedSymbol) return
    setLoading(true)
    setError(null)
    try {
      const limit = LIMITS[timeframe] ?? 100
      const data = await getCandles(selectedSymbol, timeframe, limit)
      const list: OHLCCandle[] = (data.candles ?? data ?? []).map((c: any) => ({
        time: c.time,
        open: Number(c.open),
        high: Number(c.high),
        low: Number(c.low),
        close: Number(c.close),
        volume: Number(c.volume ?? 0),
      }))
      setCandles(list)
      setLastUpdated(new Date())
    } catch (err: any) {
      setError(err.message ?? 'Failed to fetch candle data')
      setCandles([])
    } finally {
      setLoading(false)
    }
  }, [selectedSymbol, timeframe])

  // Fetch on symbol/timeframe change + auto-refresh
  // Also auto-seed from Yahoo Finance if no candles exist yet
  useEffect(() => {
    if (!selectedSymbol) return
    const run = async () => {
      await fetchCandles()
      // If still empty after fetch, auto-seed
      setCandles(prev => {
        if (prev.length === 0 && !seeding) {
          // Trigger auto-seed asynchronously
          setTimeout(() => handleSeed(), 200)
        }
        return prev
      })
    }
    run()
    if (intervalRef.current) clearInterval(intervalRef.current)
    if (['1min', '5min', '15min', '30min'].includes(timeframe)) {
      intervalRef.current = setInterval(fetchCandles, 60_000)
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current) }
  }, [fetchCandles, selectedSymbol, timeframe])

  // Seed from Yahoo Finance
  const handleSeed = async () => {
    if (!selectedSymbol || seeding) return
    setSeeding(true)
    setSeedResult(null)
    setError(null)
    try {
      const res = await seedCandles(selectedSymbol, timeframe)
      const srcLabel = res.source === 'upstox' ? '📡 Upstox'
        : res.source === 'upstox_intraday' ? '📡 Upstox Intraday'
        : '📦 Yahoo Finance'
      setSeedResult(
        `✅ ${res.candles_seeded} candles via ${srcLabel} · ${res.symbol} · ${timeframe}`
      )
      await fetchCandles()
    } catch (err: any) {
      setError(`Seed failed: ${err.message}`)
    } finally {
      setSeeding(false)
    }
  }

  // Fetch live intraday from Upstox
  const handleIntraday = async () => {
    if (!selectedSymbol || seedingIntraday) return
    setSeedingIntraday(true)
    setSeedResult(null)
    setError(null)
    try {
      const res = await seedIntraday(selectedSymbol, timeframe)
      setSeedResult(
        `📡 Upstox intraday: ${res.candles_seeded} candles for ${res.symbol} · ${timeframe}`
      )
      await fetchCandles()
    } catch (err: any) {
      setError(`Intraday fetch failed: ${err.message ?? 'Upstox token required'}`)
    } finally {
      setSeedingIntraday(false)
    }
  }

  // Derived stats
  const latest = candles.at(-1)
  const first = candles.at(0)
  const ltp = latest?.close ?? 0
  const dayOpen = first?.open ?? 0
  const dayHigh = candles.length > 0 ? Math.max(...candles.map(c => c.high)) : 0
  const dayLow  = candles.length > 0 ? Math.min(...candles.map(c => c.low))  : 0
  const totalVol = candles.reduce((s, c) => s + (c.volume ?? 0), 0)
  const change = ltp - dayOpen
  const changePct = dayOpen > 0 ? (change / dayOpen) * 100 : 0
  const isBull = change >= 0

  const lastUpdatedStr = lastUpdated
    ? lastUpdated.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '—'

  const fmtVol = (v: number) =>
    v > 1_000_000 ? `${(v / 1_000_000).toFixed(2)}M`
    : v > 1_000   ? `${(v / 1_000).toFixed(1)}K`
    : v.toString()

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <BarChart2 className="w-6 h-6 text-blue-400" />
            Market Data
          </h1>
          <p className="text-gray-400 text-sm mt-0.5">Live OHLCV candlestick charts · NSE</p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {/* Symbol */}
          <select
            value={selectedSymbol}
            onChange={e => { setSelectedSymbol(e.target.value); setSeedResult(null) }}
            className="bg-gray-900 border border-gray-700 text-white text-sm rounded-lg px-3 py-2
                       focus:outline-none focus:border-blue-500 hover:border-gray-600 transition-colors min-w-[140px]"
          >
            {symbols.length === 0 && <option value="">No symbols</option>}
            {symbols.map(s => <option key={s.id} value={s.trading_symbol}>{s.trading_symbol}</option>)}
          </select>

          {/* Timeframe */}
          <div className="flex items-center gap-1 bg-gray-900 border border-gray-800 rounded-lg p-1">
            {TIMEFRAMES.map(tf => (
              <button key={tf.value} onClick={() => { setTimeframe(tf.value); setSeedResult(null) }}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                  timeframe === tf.value ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'
                }`}>
                {tf.label}
              </button>
            ))}
          </div>

          {/* Refresh */}
          <button onClick={fetchCandles} disabled={loading}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-gray-900 border border-gray-700
                       text-sm text-gray-400 hover:text-white hover:border-gray-600 transition-colors disabled:opacity-50">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>

          {/* Fetch Live (Upstox) */}
          <button onClick={handleIntraday} disabled={seedingIntraday || !selectedSymbol}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-emerald-700 hover:bg-emerald-600
                       text-sm text-white font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
            <Activity className={`w-3.5 h-3.5 ${seedingIntraday ? 'animate-pulse' : ''}`} />
            {seedingIntraday ? 'Fetching…' : 'Live Today'}
          </button>

          {/* Fetch History (Upstox or Yahoo Finance) */}
          <button onClick={handleSeed} disabled={seeding || !selectedSymbol}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500
                       text-sm text-white font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
            <Download className={`w-3.5 h-3.5 ${seeding ? 'animate-bounce' : ''}`} />
            {seeding ? 'Fetching…' : 'Fetch History'}
          </button>
        </div>
      </div>

      {/* Seed result banner */}
      {seedResult && (
        <div className="flex items-center gap-2 px-4 py-3 bg-emerald-950/50 border border-emerald-800 rounded-xl text-emerald-300 text-sm">
          <CheckCircle className="w-4 h-4 shrink-0" />
          {seedResult}
        </div>
      )}

      {/* No-symbols hint */}
      {symbols.length === 0 && !error && (
        <div className="px-4 py-3 bg-yellow-950/40 border border-yellow-900 rounded-xl text-yellow-400 text-sm">
          ⚠️ No symbols found. Go to <strong>Settings</strong> to add symbols first (e.g. RELIANCE, INFY, TCS).
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="px-4 py-3 bg-red-950/40 border border-red-900 rounded-xl text-red-400 text-sm">
          ⚠️ {error}
        </div>
      )}

      {/* Stat Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {latest ? (
          <>
            <StatCard label="LTP"    value={`₹${ltp.toFixed(2)}`}      sub={selectedSymbol}         color={isBull ? 'text-emerald-400' : 'text-red-400'} />
            <StatCard label="Change" value={`${isBull?'+':''}${change.toFixed(2)}`} sub={`${isBull?'+':''}${changePct.toFixed(2)}%`} color={isBull ? 'text-emerald-400' : 'text-red-400'} />
            <StatCard label="Open"   value={`₹${dayOpen.toFixed(2)}`} />
            <StatCard label="High"   value={`₹${dayHigh.toFixed(2)}`}  color="text-emerald-400" />
            <StatCard label="Low"    value={`₹${dayLow.toFixed(2)}`}   color="text-red-400" />
            <StatCard label="Volume" value={fmtVol(totalVol)}           color="text-blue-400" />
          </>
        ) : (
          Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="bg-gray-900/40 border border-gray-800 rounded-xl p-4 h-16 animate-pulse" />
          ))
        )}
      </div>

      {/* Chart Panel */}
      <div className="bg-gray-950/80 border border-gray-800 rounded-xl p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            {latest
              ? isBull
                ? <TrendingUp className="w-5 h-5 text-emerald-400" />
                : <TrendingDown className="w-5 h-5 text-red-400" />
              : <Activity className="w-5 h-5 text-gray-500" />
            }
            <span className="font-semibold">{selectedSymbol || 'No symbol'} · {timeframe}</span>
            {candles.length > 0 && <span className="text-xs text-gray-500">{candles.length} candles</span>}
          </div>
          <div className="flex items-center gap-3 text-xs text-gray-500">
            {['1min','5min','15min','30min'].includes(timeframe) && (
              <span className="flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                Auto-refresh 60s
              </span>
            )}
            {lastUpdated && <span>Updated {lastUpdatedStr}</span>}
          </div>
        </div>

        {/* Empty state with seed CTA */}
        {candles.length === 0 && !loading && !error && selectedSymbol && (
          <div className="flex flex-col items-center justify-center h-64 gap-4 text-gray-500">
            <span className="text-5xl">📊</span>
            <p className="text-sm">No candle data for <strong className="text-gray-300">{selectedSymbol}</strong> · {timeframe}</p>
            <p className="text-xs text-gray-600 text-center max-w-xs">
              Click <strong className="text-blue-400">Fetch History</strong> above to pull historical OHLCV data from Yahoo Finance instantly.
            </p>
            <button onClick={handleSeed} disabled={seeding}
              className="flex items-center gap-2 px-5 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500
                         text-sm text-white font-medium transition-colors disabled:opacity-50">
              <Download className="w-4 h-4" />
              {seeding ? 'Fetching from Yahoo Finance…' : `Fetch ${timeframe} History`}
            </button>
          </div>
        )}

        {loading && candles.length === 0 && (
          <div className="flex items-center justify-center h-64 text-gray-500 gap-3">
            <RefreshCw className="w-5 h-5 animate-spin" />
            Loading candles…
          </div>
        )}

        {candles.length > 0 && <CandlestickChart candles={candles} timeframe={timeframe} />}
      </div>

      {/* Volume strip */}
      {candles.length > 0 && (
        <div className="bg-gray-950/80 border border-gray-800 rounded-xl p-5">
          <h3 className="text-sm font-medium text-gray-400 mb-4">Volume</h3>
          <div className="flex items-end gap-px h-16 w-full overflow-hidden">
            {candles.map((c, i) => {
              const maxVol = Math.max(...candles.map(x => x.volume ?? 0)) || 1
              const pct = ((c.volume ?? 0) / maxVol) * 100
              return (
                <div key={i} title={`Vol: ${c.volume?.toLocaleString()}`}
                  className="flex-1 min-w-0 rounded-sm transition-all"
                  style={{
                    height: `${Math.max(4, pct)}%`,
                    backgroundColor: c.close >= c.open ? 'rgba(16,185,129,0.5)' : 'rgba(239,68,68,0.5)',
                  }} />
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
