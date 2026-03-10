'use client'

import { useEffect, useState, useRef } from 'react'
import { Bell, X, TrendingUp, TrendingDown } from 'lucide-react'

// Interface representing the shape of alerts in Scanner's localStorage
interface SavedAlert {
    id: string;
    symbol: string;
    signal: string;
    strategy: string;
    timeframe: string;
    entry_price: number;
    stop_loss: number;
    target_price: number;
    risk_reward: number;
    rsi: number | null;
    trend: string | null;
    timestamp: string;
    data_source: string;
}

// Global SSE listener to capture signals in the background regardless of active tab
export function GlobalSignalListener() {
    const [toasts, setToasts] = useState<any[]>([])
    const sseRef = useRef<EventSource | null>(null)

    useEffect(() => {
        let es: EventSource | null = null;
        let retryTimeout: NodeJS.Timeout | null = null;
        let retryDelay = 1000;

        const connect = () => {
            const apiBase = process.env.NEXT_PUBLIC_API_URL || '/api'
            const token = localStorage.getItem('quantdss_token')
            if (!token) return

            es = new EventSource(`${apiBase}/v1/stream/signals?token=${token}`)
            sseRef.current = es

            es.onopen = () => {
                console.log("GlobalSignalListener connected")
                retryDelay = 1000; // Reset delay
            }

            es.addEventListener('signal', (e: MessageEvent) => {
                try {
                    const raw = JSON.parse(e.data)
                    const signal_type = raw.signal_type ?? raw.type
                    if (signal_type !== 'BUY' && signal_type !== 'SELL') return

                    // 1. Create a Toast
                    const newToast = {
                        id: Date.now() + Math.random(),
                        symbol: raw.symbol ?? '—',
                        signal: signal_type,
                        strategy: raw.strategy ?? '',
                        entry: raw.entry_price ?? 0,
                    }
                    setToasts((prev) => [...prev, newToast])

                    // Auto-remove toast after 5s
                    setTimeout(() => {
                        setToasts((prev) => prev.filter((t) => t.id !== newToast.id))
                    }, 5000)

                    // 2. Append to Scanner Alerts (quantdss_alerts)
                    const alert: SavedAlert = {
                        id: `${raw.symbol}_${raw.strategy}_${raw.timestamp}_${signal_type}`,
                        symbol: raw.symbol ?? '—',
                        signal: signal_type,
                        strategy: raw.strategy ?? '',
                        timeframe: "—",
                        entry_price: raw.entry_price ?? 0,
                        stop_loss: raw.stop_loss ?? 0,
                        target_price: raw.target_price ?? 0,
                        risk_reward: raw.risk_reward ?? 0,
                        rsi: null,
                        trend: null,
                        timestamp: raw.timestamp ?? new Date().toISOString(),
                        data_source: 'live_engine'
                    }

                    try {
                        const existingStr = localStorage.getItem("quantdss_alerts")
                        let existing: SavedAlert[] = existingStr ? JSON.parse(existingStr) : []
                        existing.unshift(alert) // Add to top
                        if (existing.length > 200) existing = existing.slice(0, 200)
                        localStorage.setItem("quantdss_alerts", JSON.stringify(existing))
                        window.dispatchEvent(new Event("quantdss_alerts_updated"))
                    } catch (e) {
                        console.error("GlobalSignalListener failed to save to storage", e)
                    }
                } catch (err) { }
            })

            es.onerror = () => {
                console.warn(`GlobalSignalListener disconnected, retrying in ${retryDelay}ms...`);
                es?.close();
                retryTimeout = setTimeout(() => {
                    retryDelay = Math.min(retryDelay * 2, 30000); // Cap at 30s
                    connect();
                }, retryDelay);
            }
        };

        connect();

        return () => {
            if (retryTimeout) clearTimeout(retryTimeout);
            es?.close();
        }
    }, [])

    function removeToast(id: number) {
        setToasts((prev) => prev.filter((t) => t.id !== id))
    }

    // Render floating toasts at bottom-right
    if (toasts.length === 0) return null

    return (
        <div className="fixed bottom-6 right-6 z-[9999] flex flex-col gap-3 pointer-events-none">
            {toasts.map((toast) => {
                const isBuy = toast.signal === 'BUY'
                return (
                    <div
                        key={toast.id}
                        className={`pointer-events-auto w-80 shadow-2xl rounded-xl border p-4 bg-gray-900/95 backdrop-blur-md transform transition-all duration-300 ${isBuy ? 'border-emerald-800' : 'border-red-800'
                            }`}
                    >
                        <div className="flex justify-between items-start mb-2">
                            <div className="flex items-center gap-2">
                                <div className={`p-1.5 rounded-md ${isBuy ? 'bg-emerald-950/50 text-emerald-400' : 'bg-red-950/50 text-red-400'}`}>
                                    {isBuy ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />}
                                </div>
                                <span className="font-bold text-white text-sm">
                                    {toast.symbol} {toast.signal}
                                </span>
                            </div>
                            <button
                                onClick={() => removeToast(toast.id)}
                                className="text-gray-500 hover:text-white transition-colors"
                            >
                                <X className="w-4 h-4" />
                            </button>
                        </div>

                        <div className="flex items-center justify-between text-sm">
                            <span className="text-gray-400 bg-gray-800 px-2 py-0.5 rounded textxs">{toast.strategy}</span>
                            <span className="font-mono font-semibold text-white">Entry: ₹{toast.entry.toFixed(2)}</span>
                        </div>
                    </div>
                )
            })}
        </div>
    )
}
