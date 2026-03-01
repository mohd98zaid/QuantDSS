'use client'

import { useEffect, useState } from 'react'
import { getHealth, getRiskState, getBrokerHealth } from '@/lib/api'

interface KPIData {
  todayPnl: number
  signalsToday: number
  winRate: string
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

export default function DashboardPage() {
  const [kpi, setKpi] = useState<KPIData>({
    todayPnl: 0,
    signalsToday: 0,
    winRate: '—',
    riskStatus: 'ACTIVE',
    isHalted: false,
  })
  const [signals, setSignals] = useState<SignalEvent[]>([])
  const [systemHealth, setSystemHealth] = useState<string>('checking...')
  const [brokerInfo, setBrokerInfo] = useState<{ adapter: string, status: string }>({ adapter: 'none', status: 'UNKNOWN' })

  useEffect(() => {
    // Check system health
    getHealth()
      .then((h) => setSystemHealth(h.status))
      .catch(() => setSystemHealth('offline'))

    // Check broker health
    getBrokerHealth()
      .then((b) => setBrokerInfo(b))
      .catch(() => setBrokerInfo({ adapter: 'error', status: 'ERROR' }))


    // Fetch risk state
    getRiskState()
      .then((state) => {
        setKpi({
          todayPnl: state.realised_pnl || 0,
          signalsToday: (state.signals_approved || 0) + (state.signals_blocked || 0) + (state.signals_skipped || 0),
          winRate: '—',
          riskStatus: state.is_halted ? 'HALTED' : 'ACTIVE',
          isHalted: state.is_halted,
        })
      })
      .catch(() => {})
  }, [])

  const kpiCards = [
    {
      label: "Today's P&L",
      value: `₹${kpi.todayPnl.toFixed(2)}`,
      color: kpi.todayPnl >= 0 ? 'text-emerald-400' : 'text-red-400',
    },
    {
      label: 'Signals Today',
      value: kpi.signalsToday.toString(),
      color: 'text-blue-400',
    },
    {
      label: 'Win Rate',
      value: kpi.winRate,
      color: 'text-gray-400',
    },
    {
      label: 'Risk Status',
      value: kpi.riskStatus,
      color: kpi.isHalted ? 'text-red-400' : 'text-emerald-400',
    },
  ]

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Dashboard</h1>
          <p className="text-gray-400 mt-1">Real-time trading overview</p>
        </div>
        <div className="flex items-center gap-4 text-sm">
          {/* Broker Badge */}
          <div className={`flex items-center gap-1.5 px-3 py-1 rounded-full border ${
            brokerInfo.adapter === 'upstox' 
              ? 'bg-blue-950/30 border-blue-900 text-blue-300'
              : brokerInfo.adapter === 'angel_one'
              ? 'bg-orange-950/30 border-orange-900 text-orange-300'
              : 'bg-gray-900 border-gray-800 text-gray-500'
          }`}>
            <span className={`w-1.5 h-1.5 rounded-full ${
              brokerInfo.status === 'CONNECTED' ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'
            }`}></span>
            <span className="font-medium capitalize">
              Broker: {brokerInfo.adapter}
            </span>
          </div>
          
          {/* System Health */}
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${
              systemHealth === 'ok' ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'
            }`}></span>
            <span className="text-gray-400">System: {systemHealth}</span>
          </div>
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {kpiCards.map((card) => (
          <div
            key={card.label}
            className="bg-surface-900 border border-gray-800 rounded-xl p-5 
                       hover:border-gray-700 transition-colors"
          >
            <p className="text-sm text-gray-400">{card.label}</p>
            <p className={`text-2xl font-bold font-mono-nums mt-1 ${card.color}`}>
              {card.value}
            </p>
          </div>
        ))}
      </div>

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Signal Feed — 2 columns */}
        <div className="lg:col-span-2 bg-surface-900 border border-gray-800 rounded-xl p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold">Live Signal Feed</h2>
            <span className="text-xs text-gray-500 bg-surface-800 px-2 py-1 rounded">
              SSE Connected
            </span>
          </div>

          {signals.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 text-gray-500">
              <span className="text-4xl mb-3">📡</span>
              <p>Waiting for signals...</p>
              <p className="text-xs mt-1">Signals appear here in real-time during market hours</p>
            </div>
          ) : (
            <div className="space-y-3 max-h-96 overflow-y-auto">
              {signals.map((sig, i) => (
                <div key={i} className={`border rounded-lg p-4 ${
                  sig.risk_status === 'APPROVED'
                    ? 'border-emerald-800 bg-emerald-950/30'
                    : sig.risk_status === 'BLOCKED'
                    ? 'border-red-800 bg-red-950/30'
                    : 'border-yellow-800 bg-yellow-950/30'
                }`}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className={`font-bold ${
                        sig.signal_type === 'BUY' ? 'text-emerald-400' : 'text-red-400'
                      }`}>
                        {sig.signal_type}
                      </span>
                      <span className="font-medium">{sig.symbol}</span>
                    </div>
                    <span className={`text-xs px-2 py-0.5 rounded ${
                      sig.risk_status === 'APPROVED'
                        ? 'bg-emerald-900 text-emerald-300'
                        : sig.risk_status === 'BLOCKED'
                        ? 'bg-red-900 text-red-300'
                        : 'bg-yellow-900 text-yellow-300'
                    }`}>
                      {sig.risk_status}
                    </span>
                  </div>
                  <div className="flex gap-4 mt-2 text-sm text-gray-400 font-mono-nums">
                    <span>Entry: ₹{sig.entry_price?.toFixed(2)}</span>
                    <span>SL: ₹{sig.stop_loss?.toFixed(2)}</span>
                    <span>T: ₹{sig.target_price?.toFixed(2)}</span>
                    {sig.quantity && <span>Qty: {sig.quantity}</span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Risk Panel — 1 column */}
        <div className="bg-surface-900 border border-gray-800 rounded-xl p-6">
          <h2 className="text-lg font-semibold mb-4">Risk Status</h2>

          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-gray-400">Status</span>
              <span className={`font-medium ${
                kpi.isHalted ? 'text-red-400' : 'text-emerald-400'
              }`}>
                {kpi.isHalted ? '🔴 HALTED' : '🟢 ACTIVE'}
              </span>
            </div>

            <div className="flex items-center justify-between">
              <span className="text-gray-400">Daily P&L</span>
              <span className={`font-mono-nums ${
                kpi.todayPnl >= 0 ? 'text-emerald-400' : 'text-red-400'
              }`}>
                ₹{kpi.todayPnl.toFixed(2)}
              </span>
            </div>

            <div className="flex items-center justify-between">
              <span className="text-gray-400">Signals Today</span>
              <span className="font-mono-nums">{kpi.signalsToday}</span>
            </div>

            <hr className="border-gray-800" />

            <div>
              <h3 className="text-sm font-medium text-gray-400 mb-2">P&L Buffer</h3>
              <div className="w-full bg-gray-800 rounded-full h-3">
                <div
                  className={`h-3 rounded-full transition-all ${
                    kpi.todayPnl >= 0 ? 'bg-emerald-500' : 'bg-red-500'
                  }`}
                  style={{ width: `${Math.max(10, Math.min(100, 50 + kpi.todayPnl / 10))}%` }}
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
