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
      color: kpi.todayPnl >= 0 ? 'text-emerald-400 text-glow-green' : 'text-red-400 text-glow-red',
      bg: kpi.todayPnl >= 0 ? 'border-emerald-500/20 bg-emerald-950/10' : 'border-red-500/20 bg-red-950/10',
    },
    {
      label: 'Signals Today',
      value: totalSignals.toString(),
      sub: `${kpi.signalsApproved} approved · ${kpi.signalsBlocked} blocked`,
      color: 'text-blue-400 drop-shadow-[0_0_12px_rgba(59,130,246,0.4)]',
      bg: 'border-blue-500/20 bg-blue-950/10',
    },
    {
      label: 'Approval Rate',
      value: winRate,
      sub: 'Signals approved',
      color: kpi.signalsApproved > 0 ? 'text-emerald-400' : 'text-slate-400',
      bg: 'border-slate-800/60',
    },
    {
      label: 'Risk Engine',
      value: kpi.isHalted ? 'HALTED' : 'ACTIVE',
      sub: kpi.isHalted ? 'Daily loss limit hit' : 'All rules passing',
      color: kpi.isHalted ? 'text-red-400 text-glow-red' : 'text-emerald-400 text-glow-green',
      bg: kpi.isHalted ? 'border-red-500/20 bg-red-950/10' : 'border-emerald-500/20 bg-emerald-950/10',
    },
  ]

  const chartSymRow = watchlist.find(w => w.trading_symbol === selectedSymbol)
  const chartIsBull = chartSymRow ? chartSymRow.change >= 0 : true

  return (
    <div className="space-y-5">
      {/* ── Page Header: IST Clock + Market Session ── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6 relative">
        <div className="relative">
          <div className="absolute -inset-1 bg-gradient-to-r from-blue-500 to-purple-500 rounded-lg blur opacity-25"></div>
          <h1 className="relative text-3xl font-display font-bold text-white tracking-tight">Dashboard</h1>
          <p className="text-gray-400 text-sm mt-1 font-medium">{date}</p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {/* IST Clock */}
          <div className="flex items-center gap-2 glass-panel rounded-lg px-4 py-2 hover:bg-slate-800/80 transition-colors">
            <span className="text-slate-400 text-xs font-semibold uppercase tracking-wider">IST</span>
            <span className="font-mono text-white text-sm font-semibold tracking-widest text-glow">{time}</span>
          </div>

          {/* Market Status */}
          <div className={`flex items-center gap-2 px-4 py-2 rounded-lg backdrop-blur-md border shadow-lg transition-all duration-300 ${
            isOpen === null
              ? 'bg-slate-900/60 border-slate-800 text-slate-400'
              : isOpen
              ? 'bg-emerald-950/40 border-emerald-500/30 text-emerald-300'
              : 'bg-red-950/30 border-red-500/30 text-red-400'
          }`}>
            <span className={`w-2.5 h-2.5 rounded-full shadow-[0_0_8px_rgba(0,0,0,0.5)] ${
              isOpen === null ? 'bg-slate-600 animate-pulse'
              : isOpen ? 'bg-emerald-400 animate-pulse shadow-emerald-500/50'
              : 'bg-red-500 shadow-red-500/50'
            }`} />
            <span className="font-semibold text-sm tracking-wide">{isOpen === null ? '…' : isOpen ? 'Market Open' : 'Market Closed'}</span>
            {countdownLabel && <span className="text-xs font-medium opacity-80 ml-1 bg-black/20 px-2 py-0.5 rounded">· {countdownLabel}</span>}
          </div>

          {/* Broker Badge */}
          <div className={`flex items-center gap-2 px-4 py-2 rounded-lg backdrop-blur-md border shadow-lg transition-all duration-300 ${
            brokerInfo.adapter === 'upstox'
              ? 'bg-blue-950/40 border-blue-500/30 text-blue-300'
              : brokerInfo.adapter === 'angel_one'
              ? 'bg-orange-950/40 border-orange-500/30 text-orange-300'
              : 'bg-slate-900/60 border-slate-800 text-slate-400'
          }`}>
            <span className={`w-2 h-2 rounded-full ${
              brokerInfo.status === 'CONNECTED' ? 'bg-emerald-400 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.6)]' : 'bg-slate-600'
            }`} />
            <span className="capitalize font-semibold text-sm tracking-wide">{brokerInfo.adapter === 'none' ? 'No Broker' : brokerInfo.adapter}</span>
          </div>

          {/* System Health */}
          <div className="flex items-center gap-2 glass-panel rounded-lg px-4 py-2 hover:bg-slate-800/80 transition-colors">
            <span className={`w-2 h-2 rounded-full ${
              systemHealth === 'ok' ? 'bg-emerald-400 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.6)]' : 'bg-red-400 shadow-[0_0_8px_rgba(239,68,68,0.6)]'
            }`} />
            <span className="text-sm font-semibold tracking-wide text-slate-300">System: {systemHealth}</span>
          </div>
        </div>
      </div>

      {/* ── KPI Cards ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5 mb-8">
        {kpiCards.map((card) => (
          <div key={card.label}
            className={`relative group overflow-hidden glass-card rounded-2xl p-6 ${card.bg}`}>
            {/* Hover subtle glow effect */}
            <div className="absolute inset-0 bg-gradient-to-br from-white/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500 rounded-2xl" />
            
            <p className="text-xs text-slate-400 uppercase tracking-widest font-semibold mb-2">{card.label}</p>
            <p className={`text-4xl font-bold font-mono-nums tracking-tight ${card.color} drop-shadow-lg group-hover:scale-[1.02] transition-transform duration-300 origin-left`}>
              {card.value}
            </p>
            <p className="text-sm text-slate-500 mt-2 font-medium">{card.sub}</p>
          </div>
        ))}
      </div>

      {/* ── Watchlist + Chart ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">

        {/* Watchlist */}
        <div className="glass-panel rounded-2xl overflow-hidden flex flex-col h-[500px]">
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800/60 bg-slate-900/40">
            <h2 className="text-sm font-semibold text-slate-200 tracking-wide uppercase">Watchlist</h2>
            <span className="text-xs font-mono text-slate-500 bg-slate-800/50 px-2 py-1 rounded-md">5min · NSE</span>
          </div>
          {watchlist.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full px-4 text-center text-slate-500 text-sm gap-3">
              <span className="text-4xl opacity-50 drop-shadow-md">📋</span>
              <p className="font-medium text-slate-400">No symbols configured</p>
              <p className="text-xs text-slate-600">Go to <strong className="text-slate-400">Settings</strong> to add symbols</p>
            </div>
          ) : (
            <div className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-slate-700 scrollbar-track-transparent divide-y divide-slate-800/40">
              {watchlist.map((row) => {
                const bull = row.change >= 0
                const isSelected = row.trading_symbol === selectedSymbol
                return (
                  <button
                    key={row.id}
                    onClick={() => setSelectedSymbol(row.trading_symbol)}
                    className={`w-full flex items-center justify-between px-5 py-4 text-left transition-all duration-200 hover:bg-slate-800/40 outline-none ${
                      isSelected ? 'bg-blue-900/20 border-l-4 border-l-blue-500 shadow-[inset_0_0_20px_rgba(59,130,246,0.05)]' : 'border-l-4 border-l-transparent'
                    }`}
                  >
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <span className={`font-bold text-sm tracking-wide ${isSelected ? 'text-blue-400' : 'text-slate-200'}`}>
                          {row.trading_symbol}
                        </span>
                        <span className="text-[10px] uppercase font-bold text-slate-600 bg-slate-800/80 px-1.5 py-0.5 rounded">{row.exchange}</span>
                      </div>
                      {row.loaded && row.ltp !== null && (
                        <span className="text-xs text-slate-500 font-mono">
                          Vol {fmtVol(row.volume)}
                        </span>
                      )}
                    </div>
                    <div className="text-right">
                      {!row.loaded ? (
                        <div className="h-4 w-16 bg-slate-800 rounded animate-pulse" />
                      ) : row.ltp !== null ? (
                        <>
                          <p className={`font-mono text-sm font-bold tracking-tight mb-1 ${bull ? 'text-emerald-400' : 'text-red-400'}`}>
                            {fmtPrice(row.ltp)}
                          </p>
                          <p className={`text-xs font-mono font-medium ${bull ? 'text-emerald-500' : 'text-red-500'}`}>
                            {bull ? '+' : ''}{row.change.toFixed(2)} ({bull ? '+' : ''}{row.changePct.toFixed(2)}%)
                          </p>
                        </>
                      ) : (
                        <span className="text-xs text-slate-600 italic">No data</span>
                      )}
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* Chart Panel */}
        <div className="lg:col-span-2 glass-panel rounded-2xl overflow-hidden flex flex-col h-[500px]">
          <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800/60 bg-slate-900/40">
            <div className="flex items-center gap-4">
              <span className="font-bold text-slate-100 tracking-wide text-lg">{selectedSymbol || 'Select a symbol'}</span>
              <span className="text-[10px] font-bold uppercase tracking-wider bg-slate-800/80 text-slate-400 px-2.5 py-1 rounded-md border border-slate-700/50">5min</span>
              {chartSymRow?.ltp !== null && chartSymRow?.ltp !== undefined && (
                <div className="flex items-baseline gap-2">
                  <span className={`text-lg font-mono font-bold tracking-tight drop-shadow-md ${chartIsBull ? 'text-emerald-400' : 'text-red-400'}`}>
                    {fmtPrice(chartSymRow.ltp)}
                  </span>
                  <span className={`text-xs font-mono font-medium ${chartIsBull ? 'text-emerald-500' : 'text-red-500'}`}>
                    {chartIsBull ? '+' : ''}{chartSymRow.changePct.toFixed(2)}%
                  </span>
                </div>
              )}
            </div>
            <span className="text-xs font-medium text-slate-500 flex items-center gap-2">
              {isOpen && <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)] inline-block" />}
              Intraday Chart
            </span>
          </div>

          <div className="flex-1 p-5 relative">
            {seedMsg && (
              <div className={`absolute top-4 left-1/2 -translate-x-1/2 z-10 text-xs px-4 py-2 rounded-full shadow-lg backdrop-blur-md border ${
                seedMsg.startsWith('✅')
                  ? 'bg-emerald-950/80 border-emerald-500/30 text-emerald-300'
                  : seedMsg.startsWith('⚠️')
                  ? 'bg-red-950/80 border-red-500/30 text-red-400'
                  : 'bg-blue-950/80 border-blue-500/30 text-blue-300'
              }`}>
                {seedMsg}
              </div>
            )}

            {(chartLoading || autoSeeding) && candles.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full gap-4 text-slate-500">
                <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin shadow-[0_0_15px_rgba(59,130,246,0.5)]" />
                <p className="text-sm font-medium animate-pulse">{autoSeeding ? 'Seeding historical data...' : 'Loading chart data...'}</p>
              </div>
            ) : candles.length > 0 ? (
              <div className="h-full w-full rounded-xl overflow-hidden border border-slate-800/30">
                <CandlestickChart candles={candles} timeframe="5min" />
              </div>
            ) : selectedSymbol ? (
              <div className="flex flex-col items-center justify-center h-full gap-4 text-slate-500">
                <span className="text-5xl opacity-50 drop-shadow-md animate-float">📊</span>
                <p className="text-sm font-medium">No chart data for <strong className="text-slate-300">{selectedSymbol}</strong></p>
                <p className="text-xs text-slate-600 bg-slate-900/50 px-3 py-1 rounded-full">Auto-seeding from broker will begin shortly...</p>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-full gap-4 text-slate-500">
                <span className="text-5xl opacity-30 drop-shadow-md">📈</span>
                <p className="text-sm font-medium">Select a symbol from the watchlist to view its chart</p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Signal Feed + Risk Panel ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">

        {/* Signal Feed */}
        <div className="lg:col-span-2 glass-panel rounded-2xl overflow-hidden flex flex-col h-[350px]">
          <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800/60 bg-slate-900/40">
            <h2 className="text-sm font-semibold text-slate-200 tracking-wide uppercase">Live Signal Feed</h2>
            <div className="flex items-center gap-2 text-xs font-semibold tracking-wide text-emerald-400 bg-emerald-950/30 px-3 py-1.5 rounded-full border border-emerald-500/20 shadow-[0_0_10px_rgba(16,185,129,0.1)]">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]" />
              SSE Connected
            </div>
          </div>

          {signals.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-slate-500 text-sm gap-4">
              <span className="text-5xl opacity-40 drop-shadow-md animate-float">📡</span>
              <p className="font-medium">Waiting for signals...</p>
              <p className="text-xs text-slate-600 bg-slate-900/50 px-4 py-1.5 rounded-full">Signals appear here in real-time during market hours</p>
            </div>
          ) : (
            <div className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-slate-700 scrollbar-track-transparent divide-y divide-slate-800/40">
              {signals.map((sig, i) => (
                <div key={i} className={`flex items-center justify-between px-6 py-4 text-sm transition-all hover:bg-slate-800/40 ${
                  sig.risk_status === 'APPROVED'
                    ? 'border-l-4 border-l-emerald-500 bg-emerald-950/5'
                    : sig.risk_status === 'BLOCKED'
                    ? 'border-l-4 border-l-red-500 bg-red-950/5'
                    : 'border-l-4 border-l-yellow-500 bg-yellow-950/5'
                }`}>
                  <div className="flex items-center gap-4">
                    <span className={`font-bold text-xs px-3 py-1 rounded-md tracking-wider shadow-sm ${
                      sig.signal_type === 'BUY'
                        ? 'bg-gradient-to-br from-emerald-500/20 to-emerald-600/20 text-emerald-400 border border-emerald-500/30'
                        : 'bg-gradient-to-br from-red-500/20 to-red-600/20 text-red-400 border border-red-500/30'
                    }`}>{sig.signal_type}</span>
                    <span className="font-bold text-slate-100 tracking-wide">{sig.symbol}</span>
                    <span className="text-slate-500 text-[11px] uppercase font-bold bg-slate-800/80 px-2 py-0.5 rounded border border-slate-700/50">{sig.strategy}</span>
                  </div>
                  <div className="flex items-center gap-6 text-xs font-mono font-medium">
                    <div className="flex flex-col items-end">
                      <span className="text-slate-400 text-[10px] uppercase mb-0.5">Entry</span>
                      <span className="text-slate-200">₹{sig.entry_price?.toFixed(2)}</span>
                    </div>
                    <div className="flex flex-col items-end">
                      <span className="text-slate-400 text-[10px] uppercase mb-0.5">Target</span>
                      <span className="text-emerald-400 drop-shadow-sm">₹{sig.target_price?.toFixed(2)}</span>
                    </div>
                    <div className="flex flex-col items-end">
                      <span className="text-slate-400 text-[10px] uppercase mb-0.5">Stop</span>
                      <span className="text-red-400 drop-shadow-sm">₹{sig.stop_loss?.toFixed(2)}</span>
                    </div>
                    
                    <span className={`text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 rounded-md border shadow-sm ml-2 ${
                      sig.risk_status === 'APPROVED' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                      : sig.risk_status === 'BLOCKED' ? 'bg-red-500/10 text-red-400 border-red-500/30'
                      : 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30'
                    }`}>{sig.risk_status}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Risk Panel */}
        <div className="glass-panel rounded-2xl overflow-hidden flex flex-col h-[350px]">
          <div className="px-6 py-4 border-b border-slate-800/60 bg-slate-900/40">
            <h2 className="text-sm font-semibold text-slate-200 tracking-wide uppercase">Risk Monitor</h2>
          </div>
          <div className="p-6 space-y-5 flex-1 flex flex-col justify-center">
            
            <div className="space-y-4">
              <div className="flex items-center justify-between group">
                <span className="text-xs text-slate-500 uppercase tracking-widest font-semibold group-hover:text-slate-400 transition-colors">Status</span>
                <span className={`text-sm font-bold tracking-wide px-3 py-1 rounded-md border shadow-sm ${
                  kpi.isHalted 
                    ? 'bg-red-500/10 text-red-400 border-red-500/30' 
                    : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                }`}>
                  {kpi.isHalted ? 'HALTED' : 'ACTIVE'}
                </span>
              </div>
              <div className="flex items-center justify-between group">
                <span className="text-xs text-slate-500 uppercase tracking-widest font-semibold group-hover:text-slate-400 transition-colors">Daily P&L</span>
                <span className={`font-mono text-sm font-bold tracking-tight ${kpi.todayPnl >= 0 ? 'text-emerald-400 text-glow-green' : 'text-red-400 text-glow-red'}`}>
                  {kpi.todayPnl >= 0 ? '+' : ''}₹{kpi.todayPnl.toFixed(2)}
                </span>
              </div>
              
              <div className="flex gap-2 pt-2">
                <div className="flex-1 bg-slate-800/50 rounded-lg p-3 border border-slate-700/50 flex flex-col items-center">
                  <span className="text-[10px] text-slate-500 uppercase font-bold mb-1">Approved</span>
                  <span className="font-mono text-lg font-bold text-emerald-400">{kpi.signalsApproved}</span>
                </div>
                <div className="flex-1 bg-slate-800/50 rounded-lg p-3 border border-slate-700/50 flex flex-col items-center">
                  <span className="text-[10px] text-slate-500 uppercase font-bold mb-1">Blocked</span>
                  <span className="font-mono text-lg font-bold text-red-400">{kpi.signalsBlocked}</span>
                </div>
                <div className="flex-1 bg-slate-800/50 rounded-lg p-3 border border-slate-700/50 flex flex-col items-center">
                  <span className="text-[10px] text-slate-500 uppercase font-bold mb-1">Skipped</span>
                  <span className="font-mono text-lg font-bold text-yellow-400">{kpi.signalsSkipped}</span>
                </div>
              </div>
            </div>

            <div className="mt-auto pt-4 border-t border-slate-800/60">
              <div className="flex justify-between text-[11px] font-bold text-slate-400 mb-2 uppercase tracking-wider">
                <span>P&L Buffer utilized</span>
                <span className="font-mono">{kpi.todayPnl >= 0 ? '+' : ''}{kpi.todayPnl.toFixed(2)}</span>
              </div>
              <div className="w-full bg-slate-800/80 rounded-full h-2.5 overflow-hidden border border-slate-700/50">
                <div
                  className={`h-full transition-all duration-1000 ease-out relative ${kpi.todayPnl >= 0 ? 'bg-gradient-to-r from-emerald-600 to-emerald-400' : 'bg-gradient-to-r from-red-600 to-red-400'}`}
                  style={{ width: `${Math.max(4, Math.min(100, 50 + kpi.todayPnl / 10))}%` }}
                >
                  <div className="absolute inset-0 bg-white/20 w-full animate-[pulse_2s_ease-in-out_infinite]"></div>
                </div>
              </div>
            </div>
            
          </div>
        </div>
      </div>
    </div>
  )
}
