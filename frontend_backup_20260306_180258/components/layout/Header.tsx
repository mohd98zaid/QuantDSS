'use client'

import { useEffect, useState } from 'react'
import { getRiskState, getBrokerHealth } from '@/lib/api'

function useISTClock() {
  const [time, setTime] = useState('')
  useEffect(() => {
    const tick = () => {
      setTime(
        new Intl.DateTimeFormat('en-IN', {
          timeZone: 'Asia/Kolkata',
          hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
        }).format(new Date())
      )
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  return time
}

function useMarketStatusAPI() {
  const [isOpen, setIsOpen] = useState<boolean | null>(null)
  const [label, setLabel] = useState('')

  useEffect(() => {
    async function fetchStatus() {
      try {
        const res = await fetch('/api/v1/health/market')
        if (!res.ok) throw new Error('fetch failed')
        const data = await res.json()
        setIsOpen(data.is_open)
        // Build a label from open/close times
        if (data.is_open) {
          const now = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }))
          const closeMin = 15 * 60 + 30
          const totalMin = now.getHours() * 60 + now.getMinutes()
          const rem = closeMin - totalMin
          if (rem > 0) setLabel(`Closes in ${Math.floor(rem / 60)}h ${rem % 60}m`)
        } else {
          setLabel('Closed')
        }
      } catch {
        // API unreachable — silently stay null
      }
    }
    fetchStatus()
    const id = setInterval(fetchStatus, 5 * 60_000) // re-check every 5 min
    return () => clearInterval(id)
  }, [])

  return { isOpen, label }
}

export function Header() {
  const time = useISTClock()
  const { isOpen, label } = useMarketStatusAPI()
  const [riskState, setRiskState] = useState<{ is_halted: boolean } | null>(null)
  const [broker, setBroker] = useState<{ adapter: string; status: string } | null>(null)

  useEffect(() => {
    getRiskState().then(setRiskState).catch(() => {})
    getBrokerHealth().then(setBroker).catch(() => {})
  }, [])

  return (
    <header className="h-14 bg-gray-900 border-b border-gray-800 flex items-center justify-between px-6 shrink-0">
      {/* Left */}
      <div className="flex items-center gap-4">
        {/* Market Status */}
        <div className={`flex items-center gap-1.5 text-xs px-2 py-1 rounded-md ${
          isOpen === null ? 'text-gray-500'
          : isOpen ? 'text-emerald-400'
          : 'text-red-400'
        }`}>
          <span className={`w-1.5 h-1.5 rounded-full ${
            isOpen === null ? 'bg-gray-600 animate-pulse'
            : isOpen ? 'bg-emerald-400 animate-pulse'
            : 'bg-red-500'
          }`} />
          <span className="font-medium">NSE {isOpen === null ? '…' : isOpen ? 'OPEN' : 'CLOSED'}</span>
          {label && <span className="text-gray-500 hidden sm:inline">· {label}</span>}
        </div>
      </div>

      {/* Right */}
      <div className="flex items-center gap-5 text-xs">
        {/* Broker */}
        {broker && broker.adapter !== 'none' && (
          <div className="flex items-center gap-1.5 text-gray-400">
            <span className={`w-1.5 h-1.5 rounded-full ${
              broker.status === 'CONNECTED' ? 'bg-emerald-400' : 'bg-red-400'
            }`} />
            <span className="capitalize">{broker.adapter}</span>
          </div>
        )}

        {/* Risk Badge */}
        <div className="text-gray-400">
          Risk:{' '}
          {riskState ? (
            riskState.is_halted
              ? <span className="text-red-400 font-semibold">HALTED</span>
              : <span className="text-emerald-400 font-semibold">ACTIVE</span>
          ) : (
            <span className="text-gray-500">…</span>
          )}
        </div>

        {/* IST Clock */}
        <div className="font-mono text-white bg-gray-800 px-2 py-0.5 rounded tracking-wider">
          {time || '—'}
        </div>
      </div>
    </header>
  )
}
