'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getHealth, getRiskState, getBrokerHealth, getMarketStatus,
  getSymbols, getCandles, seedCandles,
} from '@/lib/api'
import { CandlestickChart, OHLCCandle } from '@/components/CandlestickChart'

interface KPIData {
  todayPnl: number
  signalsApproved: number
  signalsBlocked: number
  signalsSkipped: number
  riskStatus: string
  isHalted: boolean
}

interface SignalEvent {
  signal_type: string
  symbol: string
  strategy: string
  entry_price: number
  stop_loss: number
  target_price: number
  risk_status: string
  block_reason?: string
  quantity?: number
  risk_reward?: number
  timestamp: string
}

interface WatchlistRow {
  id: number
  trading_symbol: string
  exchange: string
  ltp: number | null
  open: number | null
  change: number
  changePct: number
  volume: number
  loaded: boolean
}

// ── IST Clock ──────────────────────────────────────────────────────────────
function useISTClock() {
  const [time, setTime] = useState('')
  const [date, setDate] = useState('')
  useEffect(() => {
    const tick = () => {
      const now = new Date()
      const ist = new Intl.DateTimeFormat('en-IN', {
        timeZone: 'Asia/Kolkata',
        hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
      }).format(now)
      const d = new Intl.DateTimeFormat('en-IN', {
        timeZone: 'Asia/Kolkata',
        weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
      }).format(now)
      setTime(ist)
      setDate(d)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  return { time, date }
}

// ── Market session (holiday-aware via backend API) ──────────────────────────
function useMarketStatusAPI() {
  const [isOpen, setIsOpen] = useState<boolean | null>(null)
  const [countdownLabel, setCountdownLabel] = useState('')

  useEffect(() => {
    async function fetchStatus() {
      try {
        const data = await getMarketStatus()
        setIsOpen(data.is_open)
        if (data.is_open) {
          const now = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }))
          const rem = (15 * 60 + 30) - (now.getHours() * 60 + now.getMinutes())
          if (rem > 0) setCountdownLabel(`Closes in ${Math.floor(rem / 60)}h ${rem % 60}m`)
        } else {
          setCountdownLabel('Closed')
        }
      } catch {
        // silently stay null
      }
    }
    fetchStatus()
    const id = setInterval(fetchStatus, 5 * 60_000)
    return () => clearInterval(id)
  }, [])

  return { isOpen, countdownLabel }
}

export default function DashboardPage() {
  const { time, date } = useISTClock()
  const { isOpen, countdownLabel } = useMarketStatusAPI()

  const [kpi, setKpi] = useState<KPIData>({
    todayPnl: 0, signalsApproved: 0, signalsBlocked: 0, signalsSkipped: 0,
    riskStatus: 'ACTIVE', isHalted: false,
  })
  const [signals, setSignals] = useState<SignalEvent[]>([])
  const [systemHealth, setSystemHealth] = useState<string>('checking...')
  const [brokerInfo, setBrokerInfo] = useState<{ adapter: string; status: string }>(
    { adapter: 'none', status: 'UNKNOWN' }
  )
  const [watchlist, setWatchlist] = useState<WatchlistRow[]>([])
  const [selectedSymbol, setSelectedSymbol] = useState<string>('')
  const [candles, setCandles] = useState<OHLCCandle[]>([])
  const [chartLoading, setChartLoading] = useState(false)
  const [autoSeeding, setAutoSeeding] = useState(false)
  const [seedMsg, setSeedMsg] = useState<string | null>(null)
  const sseRef = useRef<EventSource | null>(null)

  // ── Initial data load ─────────────────────────────────────────────────────
  useEffect(() => {
    getHealth()
      .then((h) => setSystemHealth(h.status))
      .catch(() => setSystemHealth('offline'))

    getBrokerHealth()
      .then((b) => setBrokerInfo(b))
      .catch(() => setBrokerInfo({ adapter: 'error', status: 'ERROR' }))

    getRiskState()
      .then((state) => {
        setKpi({
          todayPnl: state.realised_pnl || 0,
          signalsApproved: state.signals_approved || 0,
          signalsBlocked: state.signals_blocked || 0,
          signalsSkipped: state.signals_skipped || 0,
          riskStatus: state.is_halted ? 'HALTED' : 'ACTIVE',
          isHalted: state.is_halted,
        })
      })
      .catch(() => {})

    // Load watchlist symbols
    getSymbols()
      .then((syms: any[]) => {
        if (!syms || syms.length === 0) return
        const rows: WatchlistRow[] = syms.map((s) => ({
          id: s.id,
          trading_symbol: s.trading_symbol,
          exchange: s.exchange || 'NSE',
          ltp: null, open: null, change: 0, changePct: 0, volume: 0, loaded: false,
        }))
        setWatchlist(rows)
        setSelectedSymbol(syms[0].trading_symbol)
      })
      .catch(() => {})
  }, [])

  // ── Load candle data for watchlist rows ───────────────────────────────────
  const loadWatchlistData = useCallback(async (syms: WatchlistRow[]) => {
    const updated = [...syms]
    await Promise.all(
      updated.map(async (row, i) => {
        try {
          const data = await getCandles(row.trading_symbol, '5min', 20)
          const list: OHLCCandle[] = (data.candles ?? data ?? []).map((c: any) => ({
            time: c.time,
            open: Number(c.open),
            high: Number(c.high),
            low: Number(c.low),
            close: Number(c.close),
            volume: Number(c.volume ?? 0),
          }))
          if (list.length > 0) {
            const latest = list[list.length - 1]
            const first = list[0]
            const change = latest.close - first.open
            updated[i] = {
              ...row,
              ltp: latest.close,
              open: first.open,
              change,
              changePct: first.open > 0 ? (change / first.open) * 100 : 0,
              volume: list.reduce((s, c) => s + c.volume, 0),
              loaded: true,
            }
          } else {
            updated[i] = { ...row, loaded: true }
          }
        } catch {
          updated[i] = { ...row, loaded: true }
        }
      })
    )
    setWatchlist(updated)
  }, [])

  useEffect(() => {
    if (watchlist.length > 0 && !watchlist[0].loaded) {
      loadWatchlistData(watchlist)
    }
  }, [watchlist, loadWatchlistData])

  // ── Fetch chart for selected symbol ───────────────────────────────────────
  const fetchChart = useCallback(async (sym: string) => {
    if (!sym) return
    setChartLoading(true)
    setSeedMsg(null)
    try {
      const data = await getCandles(sym, '5min', 80)
      const list: OHLCCandle[] = (data.candles ?? data ?? []).map((c: any) => ({
        time: c.time,
        open: Number(c.open),
        high: Number(c.high),
        low: Number(c.low),
        close: Number(c.close),
        volume: Number(c.volume ?? 0),
      }))
      if (list.length === 0) {
        // Auto-seed from Yahoo Finance as fallback
        setAutoSeeding(true)
        setSeedMsg(`Fetching data for ${sym}…`)
        try {
          const res = await seedCandles(sym, '5min')
          setSeedMsg(`✅ Seeded ${res.candles_seeded} candles via ${res.source === 'upstox' ? 'Upstox' : 'Yahoo Finance'} for ${sym}`)
          const data2 = await getCandles(sym, '5min', 80)
          const list2: OHLCCandle[] = (data2.candles ?? data2 ?? []).map((c: any) => ({
            time: c.time,
            open: Number(c.open),
            high: Number(c.high),
            low: Number(c.low),
            close: Number(c.close),
            volume: Number(c.volume ?? 0),
          }))
          setCandles(list2)
        } catch {
          setSeedMsg('⚠️ Could not fetch data. Check broker/connection.')
        } finally {
          setAutoSeeding(false)
        }
      } else {
        setCandles(list)
      }
    } catch {
      setCandles([])
    } finally {
      setChartLoading(false)
    }
  }, [])

  useEffect(() => {
    if (selectedSymbol) fetchChart(selectedSymbol)
  }, [selectedSymbol, fetchChart])

  // ── SSE live signal feed ──────────────────────────────────────────────────
  useEffect(() => {
    const apiBase = process.env.NEXT_PUBLIC_API_URL || '/api'
    const token = typeof window !== 'undefined'
      ? localStorage.getItem('quantdss_token')
      : null
    if (!token) return

    const es = new EventSource(`${apiBase}/v1/stream/signals?token=${token}`)
    sseRef.current = es

    // ⚠️ The backend sends NAMED events ("event: signal"), not plain "message".
    // onmessage only fires for un-named events → must use addEventListener.
    const handleSignal = (e: MessageEvent) => {
      try {
        const raw = JSON.parse(e.data)
        // backend wraps in { type, timestamp, signal_type, symbol, ... }
        const sig: SignalEvent = {
          signal_type:  raw.signal_type  ?? raw.type,
          symbol:       raw.symbol       ?? '—',
          strategy:     raw.strategy     ?? '',
          entry_price:  raw.entry_price  ?? 0,
          stop_loss:    raw.stop_loss    ?? 0,
          target_price: raw.target_price ?? 0,
          risk_status:  raw.risk_status  ?? 'APPROVED',
          risk_reward:  raw.risk_reward,
          timestamp:    raw.timestamp    ?? new Date().toISOString(),
        }
        if (sig.signal_type === 'BUY' || sig.signal_type === 'SELL') {
          setSignals((prev) => [sig, ...prev].slice(0, 50))
        }
      } catch {}
    }

    es.addEventListener('signal', handleSignal)
    // heartbeat / connected — ignored but prevents console errors
    es.addEventListener('connected', () => {})
    es.addEventListener('heartbeat', () => {})
    es.onerror = () => {} // suppress console noise on reconnect

    return () => {
      es.removeEventListener('signal', handleSignal)
      es.close()
    }
  }, [])

  // ── Formatters ─────────────────────────────────────────────────────────────
  const fmtPrice = (v: number | null) => v !== null ? `₹${v.toFixed(2)}` : '—'
  const fmtVol = (v: number) =>
    v > 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M`
    : v > 1_000 ? `${(v / 1_000).toFixed(0)}K`
    : v.toString()

  const totalSignals = kpi.signalsApproved + kpi.signalsBlocked + kpi.signalsSkipped
  const winRate = totalSignals > 0
    ? `${((kpi.signalsApproved / totalSignals) * 100).toFixed(0)}%`
    : '—'

  const kpiCards = [
    {
      label: "Today's P&L",
      value: `₹${kpi.todayPnl.toFixed(2)}`,
      sub: 'Realised',
      color: kpi.todayPnl >= 0 ? 'text-emerald-400' : 'text-red-400',
      bg: kpi.todayPnl >= 0 ? 'border-emerald-900/40' : 'border-red-900/40',
    },
    {
      label: 'Signals Today',
      value: totalSignals.toString(),
      sub: `${kpi.signalsApproved} approved · ${kpi.signalsBlocked} blocked`,
      color: 'text-blue-400',
      bg: 'border-blue-900/30',
    },
    {
      label: 'Approval Rate',
      value: winRate,
      sub: 'Signals approved',
      color: kpi.signalsApproved > 0 ? 'text-emerald-400' : 'text-gray-400',
      bg: 'border-gray-800',
    },
    {
      label: 'Risk Engine',
      value: kpi.isHalted ? '🔴 HALTED' : '🟢 ACTIVE',
      sub: kpi.isHalted ? 'Daily loss limit hit' : 'All rules passing',
      color: kpi.isHalted ? 'text-red-400' : 'text-emerald-400',
      bg: kpi.isHalted ? 'border-red-900/40' : 'border-emerald-900/30',
    },
  ]

  const chartSymRow = watchlist.find(w => w.trading_symbol === selectedSymbol)
  const chartIsBull = chartSymRow ? chartSymRow.change >= 0 : true

  return (
    <div className="space-y-5">
      {/* ── Page Header: IST Clock + Market Session ── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          <p className="text-gray-400 text-sm mt-0.5">{date}</p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {/* IST Clock */}
          <div className="flex items-center gap-2 bg-gray-900 border border-gray-800 rounded-lg px-3 py-2">
            <span className="text-gray-500 text-xs">IST</span>
            <span className="font-mono text-white text-sm font-semibold tracking-widest">{time}</span>
          </div>

          {/* Market Status */}
          <div className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm ${
            isOpen === null
              ? 'bg-gray-900 border-gray-800 text-gray-500'
              : isOpen
              ? 'bg-emerald-950/40 border-emerald-900 text-emerald-300'
              : 'bg-red-950/30 border-red-900/50 text-red-400'
          }`}>
            <span className={`w-2 h-2 rounded-full ${
              isOpen === null ? 'bg-gray-600 animate-pulse'
              : isOpen ? 'bg-emerald-400 animate-pulse'
              : 'bg-red-500'
            }`} />
            <span className="font-medium">{isOpen === null ? '…' : isOpen ? 'Market Open' : 'Market Closed'}</span>
            {countdownLabel && <span className="text-xs opacity-70">· {countdownLabel}</span>}
          </div>

          {/* Broker Badge */}
          <div className={`flex items-center gap-1.5 px-3 py-2 rounded-lg border text-xs ${
            brokerInfo.adapter === 'upstox'
              ? 'bg-blue-950/30 border-blue-900 text-blue-300'
              : brokerInfo.adapter === 'angel_one'
              ? 'bg-orange-950/30 border-orange-900 text-orange-300'
              : 'bg-gray-900 border-gray-800 text-gray-500'
          }`}>
            <span className={`w-1.5 h-1.5 rounded-full ${
              brokerInfo.status === 'CONNECTED' ? 'bg-emerald-400 animate-pulse' : 'bg-gray-500'
            }`} />
            <span className="capitalize font-medium">{brokerInfo.adapter === 'none' ? 'No Broker' : brokerInfo.adapter}</span>
          </div>

          {/* System Health */}
          <div className="flex items-center gap-1.5 text-xs text-gray-500">
            <span className={`w-1.5 h-1.5 rounded-full ${
              systemHealth === 'ok' ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'
            }`} />
            <span>System: {systemHealth}</span>
          </div>
        </div>
      </div>

      {/* ── KPI Cards ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {kpiCards.map((card) => (
          <div key={card.label}
            className={`bg-gray-900/80 border rounded-xl p-5 hover:border-gray-700 transition-colors ${card.bg}`}>
            <p className="text-xs text-gray-500 uppercase tracking-wide">{card.label}</p>
            <p className={`text-2xl font-bold font-mono-nums mt-2 ${card.color}`}>{card.value}</p>
            <p className="text-xs text-gray-600 mt-1">{card.sub}</p>
          </div>
        ))}
      </div>

      {/* ── Watchlist + Chart ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">

        {/* Watchlist */}
        <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
            <h2 className="text-sm font-semibold text-white">Watchlist</h2>
            <span className="text-xs text-gray-600">5min · NSE</span>
          </div>
          {watchlist.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 px-4 text-center text-gray-500 text-sm gap-2">
              <span className="text-3xl">📋</span>
              <p>No symbols configured</p>
              <p className="text-xs text-gray-600">Go to <strong className="text-gray-400">Settings</strong> to add symbols</p>
            </div>
          ) : (
            <div className="divide-y divide-gray-800/60">
              {watchlist.map((row) => {
                const bull = row.change >= 0
                const isSelected = row.trading_symbol === selectedSymbol
                return (
                  <button
                    key={row.id}
                    onClick={() => setSelectedSymbol(row.trading_symbol)}
                    className={`w-full flex items-center justify-between px-4 py-3 text-left transition-colors hover:bg-gray-800/50 ${
                      isSelected ? 'bg-blue-950/30' : ''
                    }`}
                  >
                    <div>
                      <div className="flex items-center gap-2">
                        {isSelected && <span className="w-1.5 h-1.5 rounded-full bg-blue-400" />}
                        <span className="font-semibold text-sm text-white">{row.trading_symbol}</span>
                        <span className="text-xs text-gray-600">{row.exchange}</span>
                      </div>
                      {row.loaded && row.ltp !== null && (
                        <span className="text-xs text-gray-500 mt-0.5 block">
                          Vol: {fmtVol(row.volume)}
                        </span>
                      )}
                    </div>
                    <div className="text-right">
                      {!row.loaded ? (
                        <div className="h-4 w-16 bg-gray-800 rounded animate-pulse" />
                      ) : row.ltp !== null ? (
                        <>
                          <p className={`font-mono text-sm font-bold ${bull ? 'text-emerald-400' : 'text-red-400'}`}>
                            {fmtPrice(row.ltp)}
                          </p>
                          <p className={`text-xs font-mono ${bull ? 'text-emerald-500' : 'text-red-500'}`}>
                            {bull ? '+' : ''}{row.change.toFixed(2)} ({bull ? '+' : ''}{row.changePct.toFixed(2)}%)
                          </p>
                        </>
                      ) : (
                        <span className="text-xs text-gray-600">No data</span>
                      )}
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* Chart Panel */}
        <div className="lg:col-span-2 bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
            <div className="flex items-center gap-3">
              <span className="font-semibold text-white">{selectedSymbol || 'Select a symbol'}</span>
              <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded">5min</span>
              {chartSymRow?.ltp !== null && chartSymRow?.ltp !== undefined && (
                <span className={`text-sm font-mono font-bold ${chartIsBull ? 'text-emerald-400' : 'text-red-400'}`}>
                  {fmtPrice(chartSymRow.ltp)}
                  <span className="text-xs ml-1">
                    {chartIsBull ? '+' : ''}{chartSymRow.changePct.toFixed(2)}%
                  </span>
                </span>
              )}
            </div>
            <span className="text-xs text-gray-600 flex items-center gap-1">
              {isOpen && <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse inline-block" />}
              Intraday (5min)
            </span>
          </div>

          <div className="p-4">
            {seedMsg && (
              <div className={`mb-3 text-xs px-3 py-2 rounded-lg border ${
                seedMsg.startsWith('✅')
                  ? 'bg-emerald-950/40 border-emerald-800 text-emerald-300'
                  : seedMsg.startsWith('⚠️')
                  ? 'bg-red-950/30 border-red-900/50 text-red-400'
                  : 'bg-blue-950/30 border-blue-900 text-blue-300'
              }`}>
                {seedMsg}
              </div>
            )}

            {(chartLoading || autoSeeding) && candles.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-64 gap-3 text-gray-500">
                <div className="w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                <p className="text-sm">{autoSeeding ? 'Seeding data…' : 'Loading chart…'}</p>
              </div>
            ) : candles.length > 0 ? (
              <CandlestickChart candles={candles} timeframe="5min" />
            ) : selectedSymbol ? (
              <div className="flex flex-col items-center justify-center h-64 gap-3 text-gray-500">
                <span className="text-4xl">📊</span>
                <p className="text-sm">No chart data for <strong className="text-gray-300">{selectedSymbol}</strong></p>
                <p className="text-xs text-gray-600">Auto-seeding will begin shortly…</p>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-64 gap-3 text-gray-500">
                <span className="text-4xl">📋</span>
                <p className="text-sm">Select a symbol from the watchlist</p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Signal Feed + Risk Panel ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">

        {/* Signal Feed */}
        <div className="lg:col-span-2 bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
            <h2 className="text-sm font-semibold text-white">Live Signal Feed</h2>
            <div className="flex items-center gap-1.5 text-xs text-emerald-400">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              SSE Connected
            </div>
          </div>

          {signals.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-gray-600 text-sm gap-2">
              <span className="text-3xl">📡</span>
              <p>Waiting for signals…</p>
              <p className="text-xs text-gray-700">Signals appear here in real-time during market hours</p>
            </div>
          ) : (
            <div className="divide-y divide-gray-800/60 max-h-72 overflow-y-auto">
              {signals.map((sig, i) => (
                <div key={i} className={`flex items-center justify-between px-5 py-3 text-sm ${
                  sig.risk_status === 'APPROVED'
                    ? 'border-l-2 border-l-emerald-500'
                    : sig.risk_status === 'BLOCKED'
                    ? 'border-l-2 border-l-red-500'
                    : 'border-l-2 border-l-yellow-500'
                }`}>
                  <div className="flex items-center gap-3">
                    <span className={`font-bold text-xs px-2 py-0.5 rounded ${
                      sig.signal_type === 'BUY'
                        ? 'bg-emerald-900/60 text-emerald-300'
                        : 'bg-red-900/60 text-red-300'
                    }`}>{sig.signal_type}</span>
                    <span className="font-medium text-white">{sig.symbol}</span>
                    <span className="text-gray-600 text-xs">{sig.strategy}</span>
                  </div>
                  <div className="flex items-center gap-4 text-xs font-mono text-gray-400">
                    <span>₹{sig.entry_price?.toFixed(2)}</span>
                    <span className="text-red-400">SL ₹{sig.stop_loss?.toFixed(2)}</span>
                    <span className="text-emerald-400">T ₹{sig.target_price?.toFixed(2)}</span>
                    <span className={`text-xs px-1.5 py-0.5 rounded ${
                      sig.risk_status === 'APPROVED' ? 'bg-emerald-900/50 text-emerald-300'
                      : sig.risk_status === 'BLOCKED' ? 'bg-red-900/50 text-red-300'
                      : 'bg-yellow-900/50 text-yellow-300'
                    }`}>{sig.risk_status}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Risk Panel */}
        <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-800">
            <h2 className="text-sm font-semibold text-white">Risk Monitor</h2>
          </div>
          <div className="p-5 space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-xs text-gray-500 uppercase tracking-wide">Status</span>
              <span className={`text-sm font-semibold ${kpi.isHalted ? 'text-red-400' : 'text-emerald-400'}`}>
                {kpi.isHalted ? '🔴 HALTED' : '🟢 ACTIVE'}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-gray-500 uppercase tracking-wide">Daily P&L</span>
              <span className={`font-mono text-sm font-bold ${kpi.todayPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {kpi.todayPnl >= 0 ? '+' : ''}₹{kpi.todayPnl.toFixed(2)}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-gray-500 uppercase tracking-wide">Approved</span>
              <span className="font-mono text-sm text-emerald-400">{kpi.signalsApproved}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-gray-500 uppercase tracking-wide">Blocked</span>
              <span className="font-mono text-sm text-red-400">{kpi.signalsBlocked}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-gray-500 uppercase tracking-wide">Skipped</span>
              <span className="font-mono text-sm text-yellow-400">{kpi.signalsSkipped}</span>
            </div>
            <hr className="border-gray-800" />
            <div>
              <div className="flex justify-between text-xs text-gray-500 mb-1.5 uppercase tracking-wide">
                <span>P&L Buffer</span>
                <span>{kpi.todayPnl >= 0 ? '+' : ''}{kpi.todayPnl.toFixed(2)}</span>
              </div>
              <div className="w-full bg-gray-800 rounded-full h-2">
                <div
                  className={`h-2 rounded-full transition-all ${kpi.todayPnl >= 0 ? 'bg-emerald-500' : 'bg-red-500'}`}
                  style={{ width: `${Math.max(4, Math.min(100, 50 + kpi.todayPnl / 10))}%` }}
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
