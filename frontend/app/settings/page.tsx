'use client'

import { useEffect, useState } from 'react'
import { getRiskConfig, updateRiskConfig, getSymbols } from '@/lib/api'

interface RiskConfig {
  risk_per_trade_pct: number
  max_daily_loss_inr: number
  max_daily_loss_pct: number
  max_account_drawdown_pct: number
  cooldown_minutes: number
  min_atr_pct: number
  max_atr_pct: number
  max_position_pct: number
  max_concurrent_positions: number
}

export default function SettingsPage() {
  const [config, setConfig] = useState<RiskConfig | null>(null)
  const [symbols, setSymbols] = useState<any[]>([])
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    getRiskConfig().then(setConfig).catch(() => {})
    getSymbols().then(setSymbols).catch(() => {})
  }, [])

  async function handleSave() {
    if (!config) return
    setSaving(true)
    try {
      await updateRiskConfig(config)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      console.error('Failed to save', e)
    } finally {
      setSaving(false)
    }
  }

  const updateField = (field: keyof RiskConfig, value: number) => {
    setConfig((prev) => prev ? { ...prev, [field]: value } : null)
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-gray-400 mt-1">Configure risk parameters, watchlist, and broker connections</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Risk Configuration */}
        <div className="bg-surface-900 border border-gray-800 rounded-xl p-6">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-lg font-semibold">Risk Parameters</h2>
            {saved && <span className="text-emerald-400 text-sm">✓ Saved</span>}
          </div>

          {config ? (
            <div className="space-y-4">
              <div>
                <label className="text-sm text-gray-400 block mb-1">Risk per Trade (%)</label>
                <input
                  type="number"
                  step="0.001"
                  value={config.risk_per_trade_pct}
                  onChange={(e) => updateField('risk_per_trade_pct', parseFloat(e.target.value))}
                  className="w-full bg-surface-800 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono-nums"
                />
              </div>
              <div>
                <label className="text-sm text-gray-400 block mb-1">Max Daily Loss (₹)</label>
                <input
                  type="number"
                  value={config.max_daily_loss_inr}
                  onChange={(e) => updateField('max_daily_loss_inr', parseFloat(e.target.value))}
                  className="w-full bg-surface-800 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono-nums"
                />
              </div>
              <div>
                <label className="text-sm text-gray-400 block mb-1">Max Daily Loss (%)</label>
                <input
                  type="number"
                  step="0.01"
                  value={config.max_daily_loss_pct}
                  onChange={(e) => updateField('max_daily_loss_pct', parseFloat(e.target.value))}
                  className="w-full bg-surface-800 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono-nums"
                />
              </div>
              <div>
                <label className="text-sm text-gray-400 block mb-1">Max Account Drawdown (%)</label>
                <input
                  type="number"
                  step="0.01"
                  value={config.max_account_drawdown_pct}
                  onChange={(e) => updateField('max_account_drawdown_pct', parseFloat(e.target.value))}
                  className="w-full bg-surface-800 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono-nums"
                />
              </div>
              <div>
                <label className="text-sm text-gray-400 block mb-1">Cooldown (minutes)</label>
                <input
                  type="number"
                  value={config.cooldown_minutes}
                  onChange={(e) => updateField('cooldown_minutes', parseInt(e.target.value))}
                  className="w-full bg-surface-800 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono-nums"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-sm text-gray-400 block mb-1">Min ATR (%)</label>
                  <input
                    type="number"
                    step="0.001"
                    value={config.min_atr_pct}
                    onChange={(e) => updateField('min_atr_pct', parseFloat(e.target.value))}
                    className="w-full bg-surface-800 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono-nums"
                  />
                </div>
                <div>
                  <label className="text-sm text-gray-400 block mb-1">Max ATR (%)</label>
                  <input
                    type="number"
                    step="0.001"
                    value={config.max_atr_pct}
                    onChange={(e) => updateField('max_atr_pct', parseFloat(e.target.value))}
                    className="w-full bg-surface-800 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono-nums"
                  />
                </div>
              </div>
              <div>
                <label className="text-sm text-gray-400 block mb-1">Max Position Size (%)</label>
                <input
                  type="number"
                  step="0.01"
                  value={config.max_position_pct}
                  onChange={(e) => updateField('max_position_pct', parseFloat(e.target.value))}
                  className="w-full bg-surface-800 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono-nums"
                />
              </div>
              <div>
                <label className="text-sm text-gray-400 block mb-1">Max Concurrent Positions</label>
                <input
                  type="number"
                  value={config.max_concurrent_positions}
                  onChange={(e) => updateField('max_concurrent_positions', parseInt(e.target.value))}
                  className="w-full bg-surface-800 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono-nums"
                />
              </div>

              <button
                onClick={handleSave}
                disabled={saving}
                className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white py-2.5 rounded-lg font-medium text-sm transition-colors mt-2"
              >
                {saving ? 'Saving...' : 'Save Risk Configuration'}
              </button>
            </div>
          ) : (
            <p className="text-gray-500">Loading configuration...</p>
          )}
        </div>

        {/* Watchlist + Broker */}
        <div className="space-y-6">
          {/* Watchlist */}
          <div className="bg-surface-900 border border-gray-800 rounded-xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Watchlist</h2>
              <span className="text-xs text-gray-500">{symbols.length}/20 symbols</span>
            </div>

            {symbols.length > 0 ? (
              <div className="space-y-2">
                {symbols.map((sym: any) => (
                  <div key={sym.id} className="flex items-center justify-between bg-surface-800 rounded-lg px-3 py-2">
                    <div>
                      <span className="font-medium text-sm">{sym.trading_symbol}</span>
                      <span className="text-xs text-gray-500 ml-2">{sym.exchange}</span>
                    </div>
                    <span className={`text-xs ${sym.is_active ? 'text-emerald-400' : 'text-gray-500'}`}>
                      {sym.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-gray-500 text-sm">No symbols configured. Run the seed script.</p>
            )}
          </div>

          {/* Broker Status */}
          <div className="bg-surface-900 border border-gray-800 rounded-xl p-6">
            <h2 className="text-lg font-semibold mb-4">Broker Connection</h2>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-gray-600" />
                  <span className="text-sm">Shoonya (Primary)</span>
                </div>
                <span className="text-xs text-gray-500">Not configured</span>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-gray-600" />
                  <span className="text-sm">Angel One (Fallback)</span>
                </div>
                <span className="text-xs text-gray-500">Not configured</span>
              </div>
              <p className="text-xs text-gray-500 mt-2">
                Configure broker credentials in <code className="text-gray-400">.env</code> file
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
