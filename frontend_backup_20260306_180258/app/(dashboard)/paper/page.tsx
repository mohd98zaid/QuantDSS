'use client'

import { useEffect, useState } from 'react'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '/api'

interface PaperPosition {
  id: number
  symbol: string
  direction: string
  quantity: number
  entry_price: number
  stop_loss: number
  target_price: number
  status: string
  exit_price?: number
  realized_pnl: number
  created_at: string
}

export default function PaperTradePage() {
  const [balance, setBalance] = useState<number>(100000)
  const [positions, setPositions] = useState<PaperPosition[]>([])
  const [history, setHistory] = useState<PaperPosition[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function fetchData() {
      try {
        const token = typeof window !== 'undefined' ? localStorage.getItem('quantdss_token') : null
        const headers: Record<string, string> = {}
        if (token) headers['Authorization'] = `Bearer ${token}`

        const [balRes, openRes, histRes] = await Promise.all([
          fetch(`${API_BASE}/v1/paper/balance`, { headers }),
          fetch(`${API_BASE}/v1/paper/positions`, { headers }),
          fetch(`${API_BASE}/v1/paper/history`, { headers })
        ])

        if (balRes.ok) setBalance((await balRes.json()).paper_balance)
        if (openRes.ok) setPositions(await openRes.json())
        if (histRes.ok) setHistory(await histRes.json())
      } catch (err) {
        console.error("Failed to fetch paper trading data:", err)
      } finally {
        setLoading(false)
      }
    }
    fetchData()
    // Auto refresh every 5s for tracking active trades
    const interval = setInterval(fetchData, 5000)
    return () => clearInterval(interval)
  }, [])

  const manualClose = async (id: number, currentPrice: number) => {
    if (!confirm(`Manually close virtual trade at approx ₹${currentPrice}?`)) return
    
    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('quantdss_token') : null
      const headers: Record<string, string> = {}
      if (token) headers['Authorization'] = `Bearer ${token}`

      const res = await fetch(`${API_BASE}/v1/paper/close/${id}?exit_price=${currentPrice}`, {
        method: 'POST',
        headers,
      })
      if (res.ok) {
        window.location.reload()
      }
    } catch (err) {
      alert("Failed to manual close")
    }
  }

  const resetData = async () => {
    if (!confirm('Are you sure you want to delete all paper trades and reset the virtual balance to ₹100,000? This cannot be undone.')) return
    
    setLoading(true)
    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('quantdss_token') : null
      const headers: Record<string, string> = {}
      if (token) headers['Authorization'] = `Bearer ${token}`

      const res = await fetch(`${API_BASE}/v1/paper/data`, {
        method: 'DELETE',
        headers,
      })
      if (res.ok) {
        window.location.reload()
      } else {
        alert("Failed to reset paper trading data")
      }
    } catch (err) {
        console.error("Failed to reset data", err)
    } finally {
        setLoading(false)
    }
  }

  const exportData = async () => {
    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('quantdss_token') : null
      const headers: Record<string, string> = {}
      if (token) headers['Authorization'] = `Bearer ${token}`

      const res = await fetch(`${API_BASE}/v1/paper/export`, { headers })
      if (!res.ok) throw new Error('Export failed')

      const blob = await res.blob()
      const downloadUrl = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = downloadUrl
      a.download = 'paper_trades.csv'
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(downloadUrl)
    } catch (err) {
      console.error('Failed to export data', err)
      alert('Failed to export paper trades to CSV')
    }
  }

  if (loading) {
    return <div className="p-8 text-gray-400">Loading virtual environment...</div>
  }

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-8">
      <div>
        <h1 className="text-3xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-indigo-500">
          Paper Trading
        </h1>
        <p className="text-gray-400 mt-2">Test your strategy signals with zero risk using live LTP quotes.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-surface-800 border border-gray-700 rounded-xl p-6">
          <div className="pb-2 flex justify-between items-center">
            <h3 className="text-sm text-gray-400 uppercase tracking-wider font-semibold">Virtual Balance</h3>
            <div className="flex gap-2">
              <button
                onClick={exportData}
                className="flex items-center gap-1 text-xs px-3 py-1 bg-surface-700 hover:bg-gray-600 text-gray-300 border border-gray-600 rounded-lg transition"
                title="Download all paper trades as CSV"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                Export CSV
              </button>
              <button
                onClick={resetData}
                className="text-xs px-3 py-1 bg-red-950/40 hover:bg-red-900/60 text-red-400 border border-red-900/50 rounded-lg transition"
              >
                Reset Data
              </button>
            </div>
          </div>
          <div>
            <div className="text-3xl font-mono font-semibold text-white">
              ₹ {balance.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
            </div>
            <p className="text-xs text-blue-400 mt-2">Dummy Capital starting at 1Lakh</p>
          </div>
        </div>
      </div>

      <div className="space-y-4">
        <h2 className="text-xl font-semibold text-white border-b border-gray-800 pb-2">Active Positions</h2>
        {positions.length === 0 ? (
          <p className="text-gray-500 py-4 italic">No active virtual positions. Go to the Scanner and click "Paper Trade".</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-gray-800">
            <table className="w-full text-left text-sm whitespace-nowrap">
              <thead className="bg-surface-800 text-gray-400 uppercase text-xs">
                <tr>
                  <th className="px-4 py-3 font-medium">Symbol</th>
                  <th className="px-4 py-3 font-medium">Type</th>
                  <th className="px-4 py-3 font-medium text-right">Qty</th>
                  <th className="px-4 py-3 font-medium text-right">Entry</th>
                  <th className="px-4 py-3 font-medium text-right text-red-400">SL</th>
                  <th className="px-4 py-3 font-medium text-right text-emerald-400">Target</th>
                  <th className="px-4 py-3 font-medium text-center">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800">
                {positions.map(pos => (
                  <tr key={pos.id} className="hover:bg-surface-800/50 transition-colors">
                    <td className="px-4 py-3 font-medium text-white">{pos.symbol}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-md text-xs font-bold ${pos.direction === 'BUY' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-800' : 'bg-red-500/10 text-red-400 border border-red-800'}`}>
                        {pos.direction}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-gray-300 text-right">{pos.quantity}</td>
                    <td className="px-4 py-3 font-mono text-gray-300 text-right">₹{pos.entry_price.toFixed(2)}</td>
                    <td className="px-4 py-3 font-mono text-red-400 text-right">₹{pos.stop_loss.toFixed(2)}</td>
                    <td className="px-4 py-3 font-mono text-emerald-400 text-right">₹{pos.target_price.toFixed(2)}</td>
                    <td className="px-4 py-3 text-center">
                      <button 
                        onClick={() => manualClose(pos.id, pos.direction === 'BUY' ? pos.target_price*0.99 : pos.target_price*1.01)}
                        className="text-xs px-3 py-1 bg-surface-700 hover:bg-gray-600 rounded text-red-400 transition"
                      >
                        Force Exit
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      
      <div className="space-y-4">
        <h2 className="text-xl font-semibold text-white border-b border-gray-800 pb-2">Trade History</h2>
        {history.length === 0 ? (
          <p className="text-gray-500 py-4 italic">No completed trades yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-gray-800">
            <table className="w-full text-left text-sm whitespace-nowrap flex-grow">
              <thead className="bg-surface-800 text-gray-400 uppercase text-xs">
                <tr>
                  <th className="px-4 py-3 font-medium">Symbol</th>
                  <th className="px-4 py-3 font-medium">Result</th>
                  <th className="px-4 py-3 font-medium text-right">Entry</th>
                  <th className="px-4 py-3 font-medium text-right">Exit</th>
                  <th className="px-4 py-3 font-medium text-right">Net P&L</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800">
                {history.map(pos => (
                   <tr key={pos.id} className="hover:bg-surface-800/50 transition-colors">
                   <td className="px-4 py-3 font-medium text-gray-300">{pos.symbol}({pos.direction})</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-md text-xs font-bold ${pos.realized_pnl > 0 ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/50" : "bg-red-500/10 text-red-400 border border-red-500/50"}`}>
                        {pos.realized_pnl > 0 ? 'WIN' : 'LOSS'}
                      </span>
                    </td>
                   <td className="px-4 py-3 font-mono text-gray-400 text-right">₹{pos.entry_price.toFixed(2)}</td>
                   <td className="px-4 py-3 font-mono text-gray-400 text-right">₹{pos.exit_price?.toFixed(2)}</td>
                   <td className={`px-4 py-3 font-mono text-right ${pos.realized_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                     {pos.realized_pnl >= 0 ? '+' : ''}₹{pos.realized_pnl.toFixed(2)}
                   </td>
                 </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

    </div>
  )
}
