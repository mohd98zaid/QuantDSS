"use client";

import { useState, useEffect } from "react";
import { CandlestickChart, OHLCCandle } from "@/components/CandlestickChart";
import { getToken } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export default function ReplayPage() {
    const [symbol, setSymbol] = useState("NSE_EQ|INE002A01018");
    const [startDate, setStartDate] = useState("2023-01-01");
    const [endDate, setEndDate] = useState("2023-01-31");
    const [timeframe, setTimeframe] = useState("5min");
    const [isPlaying, setIsPlaying] = useState(false);
    const [candles, setCandles] = useState<OHLCCandle[]>([]);

    useEffect(() => {
        if (!isPlaying) return;

        const eventSource = new EventSource(`${API_BASE}/v1/stream`);

        eventSource.onmessage = (event) => {
            try {
                const payload = JSON.parse(event.data);
                if (payload.type === "CANDLE" && payload.data) {
                    setCandles((prev) => {
                        const newObj = payload.data as OHLCCandle;
                        const filtered = prev.filter(c => c.time !== newObj.time);
                        return [...filtered, newObj].slice(-200);
                    });
                }
            } catch (err) {
                // ignore JSON parse errors from heartbeat/keepalive
            }
        };

        eventSource.onerror = () => {
            setIsPlaying(false);
            eventSource.close();
        };

        return () => {
            eventSource.close();
        };
    }, [isPlaying]);

    const handleStart = async () => {
        try {
            setCandles([]);
            setIsPlaying(true);
            const res = await fetch(`${API_BASE}/v1/replay/start`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${getToken()}`
                },
                body: JSON.stringify({
                    symbol,
                    start_date: startDate,
                    end_date: endDate,
                    timeframe
                })
            });
            if (!res.ok) throw new Error("Failed to start replay");
        } catch (error) {
            console.error(error);
            setIsPlaying(false);
        }
    };

    const handleStop = async () => {
        try {
            setIsPlaying(false);
            await fetch(`${API_BASE}/v1/replay/stop`, {
                method: "POST",
                headers: { "Authorization": `Bearer ${getToken()}` },
            });
        } catch (error) {
            console.error(error);
        }
    };

    return (
        <div className="p-6 space-y-6 max-w-7xl mx-auto">
            <div>
                <h1 className="text-2xl font-bold text-white">Market Replay</h1>
                <p className="text-gray-400">Simulate historical market conditions for strategy validation.</p>
            </div>

            <div className="bg-gray-800 border border-gray-700 rounded-lg p-5 flex flex-wrap gap-4 items-end shadow-lg">
                <div className="flex flex-col gap-1.5 flex-1 min-w-[200px]">
                    <label className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Symbol</label>
                    <input
                        type="text"
                        className="bg-gray-900 border border-gray-600 focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 rounded px-3 py-2 text-sm text-white transition-colors"
                        value={symbol}
                        onChange={(e) => setSymbol(e.target.value)}
                        placeholder="e.g. NSE_EQ|INE002A01018"
                    />
                </div>
                <div className="flex flex-col gap-1.5">
                    <label className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Start Date</label>
                    <input
                        type="date"
                        className="bg-gray-900 border border-gray-600 focus:border-emerald-500 rounded px-3 py-2 text-sm text-white"
                        value={startDate}
                        onChange={(e) => setStartDate(e.target.value)}
                    />
                </div>
                <div className="flex flex-col gap-1.5">
                    <label className="text-xs font-semibold text-gray-400 uppercase tracking-wider">End Date</label>
                    <input
                        type="date"
                        className="bg-gray-900 border border-gray-600 focus:border-emerald-500 rounded px-3 py-2 text-sm text-white"
                        value={endDate}
                        onChange={(e) => setEndDate(e.target.value)}
                    />
                </div>
                <div className="flex flex-col gap-1.5">
                    <label className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Timeframe</label>
                    <select
                        className="bg-gray-900 border border-gray-600 focus:border-emerald-500 rounded px-3 py-2 text-sm text-white"
                        value={timeframe}
                        onChange={(e) => setTimeframe(e.target.value)}
                    >
                        <option value="1min">1 Minute</option>
                        <option value="3min">3 Minutes</option>
                        <option value="5min">5 Minutes</option>
                        <option value="15min">15 Minutes</option>
                    </select>
                </div>

                <div className="flex gap-2 ml-auto">
                    <button
                        onClick={handleStart}
                        disabled={isPlaying}
                        className="bg-emerald-600 hover:bg-emerald-500 text-white px-5 py-2 rounded font-medium text-sm disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-1.5 shadow-sm"
                    >
                        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><path d="M4 4l12 6-12 6z" /></svg>
                        Start
                    </button>
                    <button
                        onClick={() => setIsPlaying(false)}
                        disabled={!isPlaying}
                        className="bg-amber-600 hover:bg-amber-500 text-white px-5 py-2 rounded font-medium text-sm disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-1.5 shadow-sm"
                    >
                        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><path d="M5 4h3v12H5V4zm7 0h3v12h-3V4z" /></svg>
                        Pause
                    </button>
                    <button
                        onClick={handleStop}
                        className="bg-red-600 hover:bg-red-500 text-white px-5 py-2 rounded font-medium text-sm transition-colors flex items-center gap-1.5 shadow-sm"
                    >
                        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><path fillRule="evenodd" d="M3 3h14v14H3V3z" clipRule="evenodd" /></svg>
                        Stop
                    </button>
                </div>
            </div>

            <div className="bg-gray-800 border border-gray-700 rounded-lg p-5 shadow-lg relative min-h-[400px]">
                <div className="flex justify-between items-center mb-6">
                    <h2 className="text-lg font-medium text-white flex items-center gap-2">
                        Replay Visualization
                        {isPlaying && (
                            <span className="flex h-3 w-3 relative">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                                <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
                            </span>
                        )}
                    </h2>
                    <span className="text-xs text-gray-500 bg-gray-900 px-3 py-1 rounded-full border border-gray-700">
                        {candles.length} Candles Buffered
                    </span>
                </div>

                <div className="rounded border border-gray-700/50 bg-gray-900/50 pt-4">
                    <CandlestickChart candles={candles} timeframe={timeframe} />
                </div>
            </div>
        </div>
    );
}
