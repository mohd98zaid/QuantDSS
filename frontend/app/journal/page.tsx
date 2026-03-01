'use client'

import { useState } from 'react'

export default function JournalPage() {
  const [period, setPeriod] = useState('today')

  const periods = ['today', 'week', 'month', 'all']

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Trade Journal</h1>
          <p className="text-gray-400 mt-1">Log and review trade outcomes</p>
        </div>
        <button className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
          + Log Trade
        </button>
      </div>

      {/* Period Selector */}
      <div className="flex gap-2">
        {periods.map((p) => (
          <button
            key={p}
            onClick={() => setPeriod(p)}
            className={`px-4 py-2 rounded-lg text-sm font-medium capitalize transition-colors ${
              period === p
                ? 'bg-blue-600 text-white'
                : 'bg-surface-900 text-gray-400 hover:text-white border border-gray-800'
            }`}
          >
            {p}
          </button>
        ))}
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          { label: 'Net P&L', value: '₹0.00', color: 'text-gray-400' },
          { label: 'Win Rate', value: '—', color: 'text-gray-400' },
          { label: 'Total Trades', value: '0', color: 'text-gray-400' },
          { label: 'Avg P&L/Trade', value: '₹0.00', color: 'text-gray-400' },
        ].map((card) => (
          <div key={card.label} className="bg-surface-900 border border-gray-800 rounded-xl p-4">
            <p className="text-xs text-gray-500">{card.label}</p>
            <p className={`text-lg font-bold font-mono-nums mt-1 ${card.color}`}>{card.value}</p>
          </div>
        ))}
      </div>

      {/* Trade Table */}
      <div className="bg-surface-900 border border-gray-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-gray-400 text-left">
              <th className="p-4">Date</th>
              <th className="p-4">Symbol</th>
              <th className="p-4">Direction</th>
              <th className="p-4">Entry</th>
              <th className="p-4">Exit</th>
              <th className="p-4">Qty</th>
              <th className="p-4">P&L</th>
              <th className="p-4">Notes</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td colSpan={8} className="p-12 text-center text-gray-500">
                <span className="text-3xl block mb-2">📝</span>
                No trades logged yet. Click &ldquo;+ Log Trade&rdquo; to add your first entry.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}
