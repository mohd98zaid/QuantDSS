"use client";

import { useRef, useState, useEffect, useCallback } from "react";
import { scanSymbol, executePaperTradeApi } from "@/lib/api";
import {
  Search,
  Loader2,
  TrendingUp,
  TrendingDown,
  Zap,
  RefreshCw,
  Clock,
  Bell,
  Trash2,
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

export interface SavedAlert {
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

export function saveAlertsToLocal(newAlerts: SavedAlert[]) {
  try {
    const existingStr = localStorage.getItem("quantdss_alerts");
    let existing: SavedAlert[] = existingStr ? JSON.parse(existingStr) : [];
    let added = false;
    for (const alert of newAlerts) {
      if (!existing.some((e) => e.id === alert.id)) {
        existing.push(alert);
        added = true;
      } else {
        const idx = existing.findIndex((e) => e.id === alert.id);
        if (
          idx !== -1 &&
          new Date(alert.timestamp).getTime() >
          new Date(existing[idx].timestamp).getTime()
        ) {
          existing[idx].timestamp = alert.timestamp;
          existing[idx].entry_price = alert.entry_price;
          added = true;
        }
      }
    }
    if (added) {
      existing.sort(
        (a, b) =>
          new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
      );
      if (existing.length > 200) existing = existing.slice(0, 200);
      localStorage.setItem("quantdss_alerts", JSON.stringify(existing));
      window.dispatchEvent(new Event("quantdss_alerts_updated"));
    }
  } catch (e) {
    console.error("Failed to save alerts", e);
  }
}

const PRESET_SYMBOLS: Record<string, string[]> = {
  nifty50: [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
    "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK", "LT", "AXISBANK",
    "ASIANPAINT", "MARUTI", "BAJFINANCE", "WIPRO", "HCLTECH", "SUNPHARMA",
    "TITAN", "ULTRACEMCO", "BAJAJFINSV", "NTPC", "POWERGRID", "ONGC",
    "ADANIENT", "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "HINDALCO",
    "COALINDIA", "GRASIM", "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP",
    "BRITANNIA", "EICHERMOT", "NESTLEIND", "HEROMOTOCO", "BPCL",
    "TECHM", "INDUSINDBK", "M&M", "TATACONSUM", "BAJAJ-AUTO",
    "SBILIFE", "HDFCLIFE", "ADANIPORTS", "SHREECEM", "UPL",
  ],
  banknifty: [
    "HDFCBANK", "ICICIBANK", "KOTAKBANK", "SBIN", "AXISBANK",
    "INDUSINDBK", "BANDHANBNK", "IDFCFIRSTB", "AUBANK", "FEDERALBNK",
    "PNB", "BANKBARODA",
  ],
  niftyit: [
    "TCS", "INFY", "HCLTECH", "WIPRO", "TECHM",
    "MPHASIS", "LTTS", "PERSISTENT", "COFORGE", "OFSS",
  ],
  fno_actives: [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "SBIN", "AXISBANK", "TATAMOTORS", "ADANIENT", "BAJFINANCE",
    "ONGC", "WIPRO", "MARUTI", "TITAN", "JSWSTEEL",
    "HINDALCO", "TATASTEEL", "NTPC", "COALINDIA", "M&M",
  ],
  psu: [
    "ONGC", "NTPC", "COALINDIA", "POWERGRID", "SBIN",
    "BPCL", "BHEL", "GAIL", "NHPC", "NMDC",
    "SAIL", "NALCO", "RECLTD", "PFC", "IRCTC",
    "IRFC", "CONCOR", "BEL", "HAL", "RVNL",
  ],
  midcap: [
    "INDIAMART", "PAGEIND", "ABFRL", "BATAINDIA", "VOLTAS",
    "MFSL", "POLYCAB", "ASTRAL", "ALKEM", "PIIND",
    "GLAND", "GMRINFRA", "RADICO", "LALPATHLAB", "METROPOLIS",
  ],
};

const PRESETS = [
  { key: "nifty50", label: "Nifty 50", count: 50 },
  { key: "banknifty", label: "Bank Nifty", count: 12 },
  { key: "niftyit", label: "Nifty IT", count: 10 },
  { key: "fno_actives", label: "F&O Actives", count: 20 },
  { key: "psu", label: "PSU", count: 20 },
  { key: "midcap", label: "Midcap", count: 15 },
];

const STRATEGIES = [
  { key: "ema_crossover", name: "EMA Crossover" },
  { key: "rsi_mean_reversion", name: "RSI Mean Reversion" },
  { key: "orb_vwap", name: "ORB + VWAP" },
  { key: "volume_expansion", name: "Volume Expansion" },
  { key: "trend_continuation", name: "Trend Continuation" },
  { key: "multi_strategy", name: "Multi-Strategy" },
];

const TIMEFRAMES = ["5min", "15min", "30min", "1hour", "1day"];

// ─── Auto Scan tab ─────────────────────────────────────────────────────

interface BulkResult {
  symbol: string;
  ltp: number;
  change_pct: number;
  signal: string;
  entry_price: number;
  stop_loss: number;
  target_price: number;
  risk_reward: number;
  strategy_name: string;
  rsi: number | null;
  trend: string | null;
  ema_cross: string | null;
  data_source: string;
  error: string | null;
}

interface BulkResponse {
  list_name: string;
  strategy: string;
  timeframe: string;
  total_scanned: number;
  signals_found: number;
  results: BulkResult[];
  scanned_at: string;
}

// ─── localStorage persistence key ─────────────────────────────────────
const SCANNER_STATE_KEY = "quantdss_scanner_state";

function loadScannerState() {
  if (typeof window === "undefined") return null;
  try { return JSON.parse(localStorage.getItem(SCANNER_STATE_KEY) || "null"); }
  catch { return null; }
}

function saveScannerState(state: Record<string, any>) {
  if (typeof window === "undefined") return;
  try {
    const existing = loadScannerState() ?? {};
    localStorage.setItem(SCANNER_STATE_KEY, JSON.stringify({ ...existing, ...state }));
  } catch { }
}

function AutoScanner() {
  // ── Restore state from localStorage on first render ──────────────────
  const saved = loadScannerState();

  const [presets, setPresets] = useState<Set<string>>(
    new Set(saved?.presets ?? ["nifty50"])
  );
  const [strategy, setStrategy] = useState(saved?.strategy ?? "ema_crossover");
  const [timeframe, setTimeframe] = useState(saved?.timeframe ?? "5min");
  const [signalsOnly, setSignalsOnly] = useState(saved?.signalsOnly ?? true);
  const [scanInterval, setScanInterval] = useState(saved?.scanInterval ?? 5);
  const [loading, setLoading] = useState(false);
  const [scan, setScan] = useState<BulkResponse | null>(null); // never restore scan from storage
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = localStorage.getItem("quantdss_token") || "";
    fetch(`${API_BASE}/v1/auto-trader/config`, {
      headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` }
    })
      .then(r => r.ok ? r.json() : null)
      .then(c => { if (c && c.scan_interval_minutes) setScanInterval(c.scan_interval_minutes); })
      .catch(() => { });
  }, []);

  const updateInterval = async (val: number) => {
    setScanInterval(val);
    try {
      const token = localStorage.getItem("quantdss_token") || "";
      await fetch(`${API_BASE}/v1/auto-trader/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
        body: JSON.stringify({ scan_interval_minutes: val })
      });
    } catch (e) { }
  };

  // ── Persist only lightweight settings (NOT scan results) ────────────
  useEffect(() => {
    saveScannerState({
      presets: Array.from(presets),
      strategy,
      timeframe,
      signalsOnly,
      scanInterval,
      // scan results are NOT saved here — too large, causes UI lag
    });
  }, [presets, strategy, timeframe, signalsOnly, scanInterval]);

  function togglePreset(key: string) {
    setPresets((prev) => {
      const next = new Set(prev)
      if (next.has(key)) {
        if (next.size === 1) return prev // keep at least one selected
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }

  // Merge all selected preset lists into a deduped symbol array
  const selectedSymbols = Array.from(new Set(
    PRESETS.filter((p) => presets.has(p.key)).flatMap((p) => PRESET_SYMBOLS[p.key] ?? [])
  ))

  const runScan = useCallback(async () => {
    setLoading(true);
    setError(null);
    setScan(null);
    try {
      // Bulk endpoint was removed. Fallback to mapping over symbols
      // with /v1/scanner/analyze (which is the single scan)
      const scanResults: any[] = [];
      const fetchHeaders: Record<string, string> = { "Content-Type": "application/json" };
      if (typeof window !== "undefined") {
        const token = localStorage.getItem("quantdss_token");
        if (token) fetchHeaders["Authorization"] = `Bearer ${token}`;
      }

      await Promise.all(
        selectedSymbols.map(async (sym) => {
          try {
            const symRes = await fetch(`${API_BASE}/v1/scanner/analyze`, {
              method: "POST",
              headers: fetchHeaders,
              body: JSON.stringify({
                symbol: sym,
                strategy,
                timeframe,
                candles_limit: 150
              })
            });
            if (symRes.ok) {
              const symData = await symRes.json();
              if (symData.signals && symData.signals.length > 0) {
                const first = symData.signals[0];
                scanResults.push({
                  symbol: symData.symbol,
                  ltp: symData.ltp,
                  change_pct: symData.change_pct,
                  signal: first.signal,
                  entry_price: first.entry_price,
                  stop_loss: first.stop_loss,
                  target_price: first.target_price,
                  risk_reward: first.risk_reward,
                  strategy_name: first.strategy_name,
                  rsi: symData.indicators?.rsi_14 ?? null,
                  trend: symData.indicators?.trend ?? null,
                  data_source: symData.data_source
                });
              } else if (!signalsOnly) {
                scanResults.push({
                  symbol: symData.symbol,
                  ltp: symData.ltp,
                  change_pct: symData.change_pct,
                  signal: "NEUTRAL",
                  entry_price: symData.ltp,
                  stop_loss: 0,
                  target_price: 0,
                  risk_reward: 0,
                  strategy_name: "",
                  rsi: symData.indicators?.rsi_14 ?? null,
                  trend: symData.indicators?.trend ?? null,
                  data_source: symData.data_source
                });
              }
            }
          } catch (err) {
            console.error(err);
          }
        })
      );

      const data: any = {
        list_name: Array.from(presets)[0] || "custom",
        strategy: strategy,
        timeframe: timeframe,
        total_scanned: selectedSymbols.length,
        signals_found: scanResults.filter(r => r.signal === "BUY" || r.signal === "SELL").length,
        results: scanResults.sort((a, b) => b.change_pct - a.change_pct),
        scanned_at: new Date().toISOString()
      };
      if (data.results) {
        const timestamp = new Date().toISOString();
        const newAlerts: SavedAlert[] = data.results
          .filter((r: any) => r.signal === "BUY" || r.signal === "SELL")
          .map((r: any) => ({
            id: `${r.symbol}_${data.strategy}_${data.timeframe}_${r.signal}`,
            symbol: r.symbol,
            signal: r.signal,
            strategy: data.strategy,
            timeframe: data.timeframe,
            entry_price: r.entry_price,
            stop_loss: r.stop_loss,
            target_price: r.target_price,
            risk_reward: r.risk_reward,
            rsi: r.rsi ?? null,
            trend: r.trend ?? null,
            timestamp,
            data_source: r.data_source || "unknown",
          }));
        if (newAlerts.length > 0) saveAlertsToLocal(newAlerts);
      }
    } catch (e: any) {
      setError(e.message || "Scan failed");
    } finally {
      setLoading(false);
    }
  }, [presets, selectedSymbols, strategy, timeframe, signalsOnly]);

  // ── Background Config Sync ──────────────
  const updateBackgroundConfig = async () => {
    setLoading(true);
    setError(null);
    try {
      const token = localStorage.getItem("quantdss_token") || "";
      const getRes = await fetch(`${API_BASE}/v1/auto-trader/config`, {
        headers: { "Authorization": `Bearer ${token}` }
      });
      if (!getRes.ok) throw new Error("Failed to fetch current config");
      const currentConfig = await getRes.json();

      const payload = {
        ...currentConfig,
        strategy: strategy,
        timeframe: timeframe,
        watchlist: Array.from(presets),
        scan_interval_minutes: scanInterval
      };

      const putRes = await fetch(`${API_BASE}/v1/auto-trader/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
        body: JSON.stringify(payload)
      });
      if (!putRes.ok) throw new Error("Failed to update background config");

      // Optional: show a temporary success indicator
      alert("✅ Background Scanner Configuration Updated! Background worker will now use these settings.");
    } catch (e: any) {
      setError(e.message || "Failed to update background config");
    } finally {
      setLoading(false);
    }
  };

  const exportCsv = useCallback(() => {
    if (!scan || !scan.results || scan.results.length === 0) return;

    const headers = [
      "Symbol", "LTP", "Change %", "Signal", "Entry Price", "Stop Loss", "Target", "R:R", "Strategy", "RSI", "Trend"
    ];

    const rows = scan.results.map(r => [
      r.symbol,
      r.ltp,
      r.change_pct,
      r.signal,
      r.entry_price,
      r.stop_loss,
      r.target_price,
      r.risk_reward,
      r.strategy_name,
      r.rsi ?? "",
      r.trend ?? ""
    ]);

    const csvContent = [
      headers.join(","),
      ...rows.map(row => row.join(","))
    ].join("\n");

    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const safeName = scan.list_name.replace(/\s+/g, "_").toLowerCase();
    a.download = `scan_${safeName}_${scan.strategy}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [scan]);



  return (
    <div className="space-y-5">
      {/* Config Bar */}
      <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-5">
        <div className="flex flex-wrap gap-4 items-end">
          {/* Preset */}
          <div className="flex-1 min-w-40">
            <label className="text-xs text-gray-500 uppercase tracking-wide block mb-1.5">
              Stock List
            </label>
            <div className="flex flex-wrap gap-1.5">
              {PRESETS.map((p) => {
                const isActive = presets.has(p.key)
                return (
                  <button
                    key={p.key}
                    onClick={() => togglePreset(p.key)}
                    className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${isActive
                      ? "bg-blue-600 text-white"
                      : "bg-gray-800 text-gray-400 hover:text-white border border-gray-700"
                      }`}
                  >
                    {p.label}{" "}
                    <span className="text-xs opacity-60">({p.count})</span>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Strategy */}
          <div>
            <label className="text-xs text-gray-500 uppercase tracking-wide block mb-1.5">
              Strategy
            </label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            >
              {STRATEGIES.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>

          {/* Timeframe */}
          <div>
            <label className="text-xs text-gray-500 uppercase tracking-wide block mb-1.5">
              Timeframe
            </label>
            <div className="flex gap-1">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  onClick={() => setTimeframe(tf)}
                  className={`px-2.5 py-2 rounded-lg text-xs font-medium transition-colors ${timeframe === tf
                    ? "bg-blue-600 text-white"
                    : "bg-gray-800 text-gray-400 hover:text-white border border-gray-700"
                    }`}
                >
                  {tf}
                </button>
              ))}
            </div>
          </div>



          {/* Toggle signals only */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => setSignalsOnly(!signalsOnly)}
              className={`relative w-10 h-5 rounded-full transition-colors ${signalsOnly ? "bg-blue-600" : "bg-gray-700"}`}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${signalsOnly ? "translate-x-5" : ""}`}
              />
            </button>
            <span className="text-xs text-gray-400">Signals only</span>
          </div>

          {/* Background Sync */}
          <div className="flex items-center gap-2 border-l border-gray-700 pl-4 ml-2">
            <span className="text-xs text-gray-400">Background Scan Interval (m)</span>
            <input
              type="number"
              min="1"
              max="60"
              value={scanInterval}
              onChange={(e) => setScanInterval(Math.max(1, parseInt(e.target.value) || 5))}
              className="bg-gray-800 border border-gray-700 rounded-lg w-16 px-2 py-1 text-xs focus:outline-none focus:border-blue-500 text-gray-300"
            />
            <button
              onClick={updateBackgroundConfig}
              disabled={loading}
              className="ml-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors whitespace-nowrap"
              title="Save current strategy, timeframe, and lists to the background scanner worker"
            >
              Sync to Backend
            </button>
          </div>

          {/* Scan button */}
          <button
            onClick={runScan}
            disabled={loading}
            className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white px-6 py-2.5 rounded-lg font-semibold text-sm transition-colors"
          >
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" /> Scanning…
              </>
            ) : (
              <>
                <Zap className="w-4 h-4" /> Scan Now
              </>
            )}
          </button>
        </div>

        {loading && (
          <div className="mt-4 flex items-center gap-3 text-sm text-gray-400">
            <Loader2 className="w-4 h-4 animate-spin text-blue-400" />
            Scanning <strong>{selectedSymbols.length}</strong> stocks
            {presets.size > 1 && <span className="text-gray-500">across {presets.size} lists</span>}
            concurrently via Upstox → Angel One → Yahoo fallback…
            <span className="text-gray-600 text-xs">(~5–10s)</span>
          </div>
        )}
      </div>

      {error && (
        <div className="px-4 py-3 bg-red-950/40 border border-red-900/50 rounded-xl text-red-300 text-sm">
          ⚠️ {error}
        </div>
      )}

      {/* Results */}
      {scan && (
        <div className="space-y-4">
          {/* Summary */}
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div className="flex items-center gap-4">
              <h2 className="text-lg font-bold text-white">
                {PRESETS.find((p) => p.key === scan.list_name)?.label ??
                  scan.list_name}{" "}
                — {scan.strategy}
              </h2>
              <span className="text-xs px-2 py-1 bg-gray-800 rounded">
                {scan.timeframe}
              </span>
            </div>
            <div className="flex items-center gap-4 text-sm">
              <span className="text-gray-500">
                {scan.total_scanned} scanned
              </span>
              <span className="text-gray-500 flex items-center gap-1 bg-gray-800/50 px-2 py-1 border border-gray-700/50 rounded" title="Scanned At">
                <Clock className="w-3.5 h-3.5" />
                {new Date(scan.scanned_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              </span>
              <span
                className={`font-bold ${scan.signals_found > 0 ? "text-emerald-400" : "text-gray-500"}`}
              >
                {scan.signals_found} signal{scan.signals_found !== 1 ? "s" : ""}{" "}
                found
              </span>
              <button
                onClick={exportCsv}
                className="flex items-center gap-1 text-gray-500 hover:text-white text-xs border border-gray-700/50 rounded px-2 py-1 bg-gray-800/50 hover:bg-gray-700 transition"
                title="Download scan results as CSV"
              >
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                Export CSV
              </button>
              <button
                onClick={runScan}
                disabled={loading}
                className="flex items-center gap-1 text-gray-500 hover:text-white text-xs ml-2"
              >
                <RefreshCw className="w-3 h-3" /> Rescan
              </button>
            </div>
          </div>

          {scan.results.length === 0 ? (
            <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-12 text-center">
              <span className="text-4xl block mb-3">😶</span>
              <p className="text-white font-semibold">
                No signals in {scan.list_name} right now
              </p>
              <p className="text-gray-500 text-sm mt-1">
                Try a different strategy, timeframe, or disable "signals only"
                to see all stocks
              </p>
            </div>
          ) : (
            <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-800 text-gray-500 text-xs uppercase">
                    <th className="text-left px-4 py-3">Symbol</th>
                    <th className="text-right px-4 py-3">LTP</th>
                    <th className="text-right px-4 py-3">Change</th>
                    <th className="text-center px-4 py-3">Signal</th>
                    <th className="text-right px-4 py-3">Entry</th>
                    <th className="text-right px-4 py-3">Stop Loss</th>
                    <th className="text-right px-4 py-3">Target</th>
                    <th className="text-right px-4 py-3">R:R</th>
                    <th className="text-right px-4 py-3">RSI</th>
                    <th className="text-left px-4 py-3">Trend</th>
                  </tr>
                </thead>
                <tbody>
                  {scan.results.map((r, i) => {
                    const isBuy = r.signal === "BUY";
                    const isSell = r.signal === "SELL";
                    const bull = r.change_pct >= 0;
                    return (
                      <tr
                        key={r.symbol + i}
                        className={`border-b border-gray-800/50 last:border-0 hover:bg-gray-800/30 transition-colors ${isBuy
                          ? "border-l-2 border-l-emerald-600"
                          : isSell
                            ? "border-l-2 border-l-red-600"
                            : ""
                          }`}
                      >
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            {isBuy ? (
                              <TrendingUp className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                            ) : isSell ? (
                              <TrendingDown className="w-3.5 h-3.5 text-red-400 shrink-0" />
                            ) : (
                              <span className="w-3.5" />
                            )}
                            <span className="font-semibold text-white">
                              {r.symbol}
                            </span>
                          </div>
                          {r.error && (
                            <p className="text-xs text-red-500 mt-0.5">
                              {r.error}
                            </p>
                          )}
                        </td>
                        <td className="px-4 py-3 text-right font-mono">
                          {r.ltp > 0 ? `₹${r.ltp.toFixed(2)}` : "—"}
                        </td>
                        <td
                          className={`px-4 py-3 text-right font-mono text-xs ${bull ? "text-emerald-400" : "text-red-400"}`}
                        >
                          {r.ltp > 0
                            ? `${bull ? "+" : ""}${r.change_pct.toFixed(2)}%`
                            : "—"}
                        </td>
                        <td className="px-4 py-3 text-center">
                          {isBuy || isSell ? (
                            <span
                              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-bold ${isBuy
                                ? "bg-emerald-950/60 text-emerald-300 border border-emerald-800"
                                : "bg-red-950/60 text-red-300 border border-red-800"
                                }`}
                            >
                              {r.signal}
                            </span>
                          ) : (
                            <span className="text-gray-600 text-xs">—</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-white">
                          {r.entry_price > 0
                            ? `₹${r.entry_price.toFixed(2)}`
                            : "—"}
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-red-400">
                          {r.stop_loss > 0 ? `₹${r.stop_loss.toFixed(2)}` : "—"}
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-emerald-400">
                          {r.target_price > 0
                            ? `₹${r.target_price.toFixed(2)}`
                            : "—"}
                        </td>
                        <td
                          className={`px-4 py-3 text-right font-mono font-bold ${r.risk_reward >= 2 ? "text-emerald-400" : "text-yellow-400"}`}
                        >
                          {r.risk_reward > 0 ? `1:${r.risk_reward}` : "—"}
                        </td>
                        <td
                          className={`px-4 py-3 text-right font-mono text-xs ${r.rsi
                            ? r.rsi >= 70
                              ? "text-red-400"
                              : r.rsi <= 30
                                ? "text-emerald-400"
                                : "text-gray-400"
                            : "text-gray-600"
                            }`}
                        >
                          {r.rsi ? r.rsi.toFixed(1) : "—"}
                        </td>
                        <td className="px-4 py-3">
                          <span
                            className={`text-xs ${r.trend === "UPTREND" ? "text-emerald-400" : r.trend === "DOWNTREND" ? "text-red-400" : "text-gray-600"}`}
                          >
                            {r.trend ?? "—"}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {!scan && !loading && !error && (
        <div className="flex flex-col items-center justify-center py-20 text-center gap-3 text-gray-600">
          <Zap className="w-16 h-16 opacity-20" />
          <p className="text-lg text-gray-500">
            Click Auto Scan to find signals across the market
          </p>
          <p className="text-sm">
            Scans all stocks in the selected list concurrently
          </p>
        </div>
      )}
    </div>
  );
}

// ─── Single stock scanner tab ─────────────────────────────────────────

interface ScanResult {
  symbol: string;
  timeframe: string;
  strategy: string;
  ltp: number;
  change_pct: number;
  candles_fetched: number;
  signals: {
    signal: string;
    entry_price: number;
    stop_loss: number;
    target_price: number;
    risk_reward: number;
    atr: number;
    strategy_name: string;
    candle_time: string;
  }[];
  indicators: {
    rsi_14?: number | null;
    ema_9?: number | null;
    ema_21?: number | null;
    ema_50?: number | null;
    atr_14?: number | null;
    volume_ma_20?: number | null;
    volume?: number;
    trend?: string;
    ema_cross?: string;
    rsi_zone?: string;
  };
  data_source: string;
  instrument_key: string | null;
  scanned_at: string;
}

const QUICK_SYMBOLS = [
  "RELIANCE",
  "TCS",
  "INFY",
  "HDFCBANK",
  "ICICIBANK",
  "SBIN",
  "WIPRO",
  "AXISBANK",
  "BAJFINANCE",
  "MARUTI",
  "TATAMOTORS",
  "ADANIENT",
  "NTPC",
  "ONGC",
  "POWERGRID",
  "NIFTY50",
];

function SingleScanner() {
  const [symbol, setSymbol] = useState("");
  const [strategy, setStrategy] = useState("ema_crossover");
  const [timeframe, setTimeframe] = useState("5min");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(() => loadScannerState()?.singleAutoRefresh ?? 0);

  // Persist SingleScanner autoRefresh
  useEffect(() => {
    saveScannerState({ singleAutoRefresh: autoRefresh });
  }, [autoRefresh]);
  const [suggestions, setSuggestions] = useState<any[]>([]);
  const [sugLoading, setSugLoading] = useState(false);
  const [showSug, setShowSug] = useState(false);
  const sugTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function handleSymbolChange(val: string) {
    setSymbol(val.toUpperCase());
    setShowSug(true);
    if (sugTimer.current) clearTimeout(sugTimer.current);
    if (val.length < 1) {
      setSuggestions([]);
      return;
    }
    sugTimer.current = setTimeout(async () => {
      setSugLoading(true);
      try {
        let found: any[] = [];
        const srch = val.toUpperCase();
        for (const list of Object.values(PRESET_SYMBOLS)) {
          for (const s of list) {
            if (s.includes(srch) && !found.find(f => f.symbol === s)) {
              found.push({ symbol: s, name: s });
              if (found.length >= 10) break;
            }
          }
          if (found.length >= 10) break;
        }
        setSuggestions(found);
      } catch {
        setSuggestions([]);
      } finally {
        setSugLoading(false);
      }
    }, 300);
  }

  function selectSuggestion(sym: string) {
    setSymbol(sym);
    setSuggestions([]);
    setShowSug(false);
    inputRef.current?.blur();
  }

  const handleScan = useCallback(
    async (sym?: string) => {
      const target = (typeof sym === "string" ? sym : symbol)
        .trim()
        .toUpperCase();
      if (!target) return;
      if (typeof sym === "string") setSymbol(sym);
      setSuggestions([]);
      setShowSug(false);
      setLoading(true);
      setError(null);
      setResult(null);
      try {
        const data = await scanSymbol({
          symbol: target,
          strategy,
          timeframe,
          candles_limit: 150,
        });
        setResult(data);
        if (data.signals && data.signals.length > 0) {
          const timestamp = new Date().toISOString();
          const newAlerts: SavedAlert[] = data.signals.map((sig: any) => ({
            id: `${data.symbol}_${sig.strategy_name}_${data.timeframe}_${sig.signal}`,
            symbol: data.symbol,
            signal: sig.signal,
            strategy: sig.strategy_name,
            timeframe: data.timeframe,
            entry_price: sig.entry_price,
            stop_loss: sig.stop_loss,
            target_price: sig.target_price,
            risk_reward: sig.risk_reward,
            rsi: data.indicators?.rsi_14 ?? null,
            trend: data.indicators?.trend ?? null,
            timestamp,
            data_source: data.data_source || "unknown",
          }));
          if (newAlerts.length > 0) saveAlertsToLocal(newAlerts);
        }
      } catch (e: any) {
        setError(e.message || "Scan failed");
      } finally {
        setLoading(false);
      }
    },
    [symbol, strategy, timeframe],
  );

  useEffect(() => {
    if (autoRefresh <= 0 || !symbol.trim()) return;
    const timer = setInterval(() => {
      handleScan();
    }, autoRefresh * 1000);
    return () => clearInterval(timer);
  }, [autoRefresh, symbol, handleScan]);

  const signalColor = (s: string) =>
    s === "BUY"
      ? "text-emerald-400"
      : s === "SELL"
        ? "text-red-400"
        : "text-gray-400";

  return (
    <div className="space-y-5">
      <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-5">
        <div className="flex gap-3 mb-4">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500 z-10" />
            <input
              ref={inputRef}
              type="text"
              placeholder="Search any NSE symbol · e.g. RELIANCE, HDFC, BANKNIFTY"
              value={symbol}
              onChange={(e) => handleSymbolChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleScan();
                if (e.key === "Escape") setShowSug(false);
              }}
              onFocus={() => suggestions.length > 0 && setShowSug(true)}
              onBlur={() => setTimeout(() => setShowSug(false), 150)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-10 pr-4 py-3 text-sm focus:outline-none focus:border-blue-500 uppercase font-medium"
              autoFocus
            />
            {showSug && (suggestions.length > 0 || sugLoading) && (
              <div className="absolute top-full left-0 right-0 mt-1 bg-gray-900 border border-gray-700 rounded-lg overflow-hidden z-50 shadow-2xl">
                {sugLoading && (
                  <div className="flex items-center gap-2 px-4 py-2.5 text-gray-500 text-sm">
                    <Loader2 className="w-3 h-3 animate-spin" />
                    Searching…
                  </div>
                )}
                {suggestions.map((s: any) => (
                  <button
                    key={s.key}
                    onMouseDown={() => selectSuggestion(s.symbol)}
                    className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-gray-800 text-left transition-colors"
                  >
                    <div>
                      <span className="font-semibold text-sm text-white">
                        {s.symbol}
                      </span>
                      <span className="text-xs text-gray-500 ml-2">
                        {s.name}
                      </span>
                    </div>
                    <span className="text-xs text-gray-600 shrink-0 ml-2">
                      {s.exchange}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button
            onClick={() => handleScan()}
            disabled={loading || !symbol.trim()}
            className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-6 py-3 rounded-lg font-semibold text-sm transition-colors"
          >
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Search className="w-4 h-4" />
            )}
            {loading ? "Scanning…" : "Scan"}
          </button>
        </div>

        <div className="flex flex-wrap gap-3 mb-4">
          <select
            value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 flex-1 min-w-48"
          >
            {STRATEGIES.map((s) => (
              <option key={s.key} value={s.key}>
                {s.name}
              </option>
            ))}
          </select>
          <div className="flex gap-1.5">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${timeframe === tf ? "bg-blue-600 text-white" : "bg-gray-800 text-gray-400 hover:text-white border border-gray-700"}`}
              >
                {tf}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-wrap justify-between items-center gap-2 mt-4 pt-4 border-t border-gray-800/50">
          <div className="flex flex-wrap gap-2 flex-1">
            {QUICK_SYMBOLS.map((sym) => (
              <button
                key={sym}
                onClick={() => handleScan(sym)}
                disabled={loading}
                className="text-xs px-2.5 py-1 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-gray-300 transition-colors"
              >
                {sym}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-gray-400" />
            <select
              value={autoRefresh}
              onChange={(e) => setAutoRefresh(Number(e.target.value))}
              className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:border-blue-500 text-gray-300"
            >
              <option value={0}>Auto: Off</option>
              <option value={30}>Every 30s</option>
              <option value={60}>Every 1m</option>
              <option value={300}>Every 5m</option>
            </select>
          </div>
        </div>
      </div>

      {error && (
        <div className="px-4 py-3 bg-red-950/40 border border-red-900/50 rounded-xl text-red-300 text-sm">
          ⚠️ {error}
        </div>
      )}

      {result && !loading && (
        <div className="space-y-4">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div className="flex items-center gap-4">
              <h2 className="text-3xl font-bold text-white">{result.symbol}</h2>
              <div>
                <p
                  className={`text-2xl font-bold font-mono ${result.change_pct >= 0 ? "text-emerald-400" : "text-red-400"}`}
                >
                  ₹{result.ltp.toFixed(2)}
                </p>
                <p
                  className={`text-sm font-mono ${result.change_pct >= 0 ? "text-emerald-500" : "text-red-500"}`}
                >
                  {result.change_pct >= 0 ? "+" : ""}
                  {result.change_pct.toFixed(2)}% today
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <span className="px-2 py-1 bg-gray-800 rounded">
                {result.timeframe}
              </span>
              <span className="px-2 py-1 bg-gray-800 rounded">
                {result.candles_fetched} candles
              </span>
              <span
                className={`px-2 py-1 rounded font-medium ${result.data_source === "upstox" ? "bg-blue-950/50 border border-blue-900 text-blue-300" : "bg-gray-800 text-gray-400"}`}
              >
                {result.data_source === "upstox"
                  ? "📡 Upstox"
                  : "📦 Yahoo Finance"}
              </span>
              <span className="flex items-center gap-1 text-gray-400 bg-gray-800/50 px-2 py-1 rounded border border-gray-700/50" title="Scanned At">
                <Clock className="w-3.5 h-3.5" />
                {new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              </span>
            </div>
          </div>

          {result.signals.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {result.signals.map((sig, i) => (
                <div
                  key={i}
                  className={`border rounded-xl p-5 ${sig.signal === "BUY" ? "bg-emerald-950/30 border-emerald-800" : "bg-red-950/30 border-red-800"}`}
                >
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-2">
                      {sig.signal === "BUY" ? (
                        <TrendingUp className="w-5 h-5 text-emerald-400" />
                      ) : (
                        <TrendingDown className="w-5 h-5 text-red-400" />
                      )}
                      <span
                        className={`text-xl font-bold ${signalColor(sig.signal)}`}
                      >
                        {sig.signal}
                      </span>
                    </div>
                    <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">
                      {sig.strategy_name}
                    </span>
                  </div>
                  <div className="space-y-2.5 text-sm">
                    {[
                      ["Entry", `₹${sig.entry_price.toFixed(2)}`, "text-white"],
                      [
                        "Stop Loss",
                        `₹${sig.stop_loss.toFixed(2)}`,
                        "text-red-400",
                      ],
                      [
                        "Target",
                        `₹${sig.target_price.toFixed(2)}`,
                        "text-emerald-400",
                      ],
                    ].map(([label, val, color]) => (
                      <div key={label} className="flex justify-between">
                        <span className="text-gray-500">{label}</span>
                        <span className={`font-mono font-bold ${color}`}>
                          {val}
                        </span>
                      </div>
                    ))}
                    <div className="pt-2 border-t border-gray-800 flex justify-between">
                      <span className="text-gray-500">Risk:Reward</span>
                      <span
                        className={`font-mono font-bold ${sig.risk_reward >= 2 ? "text-emerald-400" : "text-yellow-400"}`}
                      >
                        1:{sig.risk_reward}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-8 text-center">
              <span className="text-4xl block mb-3">😶</span>
              <p className="text-white font-semibold">
                No Signal on {result.symbol}
              </p>
              <p className="text-gray-500 text-sm">
                No entry conditions met right now. Try a different timeframe or
                strategy.
              </p>
            </div>
          )}
        </div>
      )}

      {!result && !loading && !error && (
        <div className="flex flex-col items-center justify-center py-20 text-center gap-3 text-gray-600">
          <Search className="w-16 h-16 opacity-20" />
          <p className="text-lg text-gray-500">
            Enter any NSE symbol and click Scan
          </p>
          <p className="text-sm">
            Works for any stock — RELIANCE, INFY, BANKNIFTY, NIFTY50…
          </p>
        </div>
      )}
    </div>
  );
}

// ─── Alerts History Tab ───────────────────────────────────────────────

function AlertsHistory() {
  const [alerts, setAlerts] = useState<SavedAlert[]>([]);

  const loadAlerts = () => {
    try {
      const stored = localStorage.getItem("quantdss_alerts");
      if (stored) setAlerts(JSON.parse(stored));
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    loadAlerts();
    window.addEventListener("quantdss_alerts_updated", loadAlerts);
    return () =>
      window.removeEventListener("quantdss_alerts_updated", loadAlerts);
  }, []);

  const clearAlerts = () => {
    if (!confirm("Are you sure you want to clear all saved alerts?")) return;
    localStorage.removeItem("quantdss_alerts");
    setAlerts([]);
  };

  const deleteAlert = (id: string) => {
    const updated = alerts.filter((a) => a.id !== id);
    localStorage.setItem("quantdss_alerts", JSON.stringify(updated));
    setAlerts(updated);
  };

  const executePaperTrade = async (alert: SavedAlert) => {
    try {
      await executePaperTradeApi({
        symbol: alert.symbol,
        instrument_key: "", // Can be filled if available
        direction: alert.signal,
        quantity: 1, // Default to 1 qty for dummy
        entry_price: alert.entry_price,
        stop_loss: alert.signal === "BUY" ? alert.entry_price * 0.99 : alert.entry_price * 1.01,
        target_price: alert.signal === "BUY" ? alert.entry_price * 1.02 : alert.entry_price * 0.98,
      });
      window.alert("Paper Trade Executed! Check the Paper Trading tab.");
    } catch (e: any) {
      window.alert("Failed: " + e.message);
    }
  };

  const exportAlertsCsv = () => {
    if (alerts.length === 0) return;

    const headers = [
      "Time", "Symbol", "Signal", "Option", "Strategy", "Timeframe",
      "Entry", "Stop Loss", "Target", "R:R", "RSI", "Trend"
    ];

    const rows = alerts.map((a) => {
      const isBuy = a.signal === "BUY";
      const optionType = isBuy ? "CALL" : "PUT";
      const date = new Date(a.timestamp);
      const timeStr = `${date.toLocaleDateString()} ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;

      return [
        `"${timeStr}"`,
        a.symbol,
        a.signal,
        optionType,
        `"${a.strategy}"`,
        a.timeframe,
        a.entry_price > 0 ? a.entry_price : "",
        a.stop_loss > 0 ? a.stop_loss : "",
        a.target_price > 0 ? a.target_price : "",
        a.risk_reward > 0 ? `1:${a.risk_reward}` : "",
        a.rsi ?? "",
        a.trend ?? ""
      ];
    });

    const csvContent = [
      headers.join(","),
      ...rows.map(row => row.join(","))
    ].join("\n");

    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `saved_alerts_${new Date().toISOString().split('T')[0]}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  if (alerts.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center gap-3 text-gray-600 bg-gray-900/80 border border-gray-800 rounded-xl">
        <Bell className="w-16 h-16 opacity-20" />
        <p className="text-lg text-gray-500">No alerts saved yet</p>
        <p className="text-sm">
          Run Auto Scan or Single Stock scans to find signals. They will
          automatically be saved here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold text-white flex items-center gap-2">
          <Bell className="w-5 h-5 text-blue-400" /> Saved Alerts ({alerts.length})
        </h2>
        <div className="flex gap-2">
          <button
            onClick={exportAlertsCsv}
            className="text-xs flex items-center gap-1 text-gray-300 hover:text-white px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors border border-gray-700/50"
            title="Download saved alerts as CSV"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
            Export CSV
          </button>
          <button
            onClick={clearAlerts}
            className="text-xs flex items-center gap-1 text-red-500 hover:text-red-400 px-3 py-1.5 bg-red-950/30 hover:bg-red-900/40 rounded-lg transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5" /> Clear All
          </button>
        </div>
      </div>

      <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-gray-500 text-xs uppercase text-left whitespace-nowrap">
                <th className="px-4 py-3">Time</th>
                <th className="px-4 py-3">Symbol</th>
                <th className="px-4 py-3 text-center">Signal</th>
                <th className="px-4 py-3 text-center">Option</th>
                <th className="px-4 py-3">Strategy</th>
                <th className="px-4 py-3 text-center">TF</th>
                <th className="px-4 py-3 text-right">Entry</th>
                <th className="px-4 py-3 text-right">Stop Loss</th>
                <th className="px-4 py-3 text-right">Target</th>
                <th className="px-4 py-3 text-right">R:R</th>
                <th className="px-4 py-3 text-right">RSI</th>
                <th className="px-4 py-3">Trend</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((a) => {
                const isBuy = a.signal === "BUY";
                const isSell = a.signal === "SELL";
                const date = new Date(a.timestamp);
                const optionType = isBuy ? "CALL" : "PUT";
                const rsiVal = a.rsi ?? null;
                return (
                  <tr
                    key={a.id}
                    className={`border-b border-gray-800/50 last:border-0 hover:bg-gray-800/30 transition-colors ${isBuy
                      ? "border-l-2 border-l-emerald-600"
                      : isSell
                        ? "border-l-2 border-l-red-600"
                        : ""
                      }`}
                  >
                    {/* Time */}
                    <td className="px-4 py-3 text-gray-400 text-xs whitespace-nowrap space-y-0.5">
                      <div className="text-gray-300 font-medium">{date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</div>
                      <div className="text-[10px] text-gray-500 uppercase flex items-center gap-1"><Clock className="w-3 h-3" /> {date.toLocaleDateString()}</div>
                    </td>

                    {/* Symbol */}
                    <td className="px-4 py-3 font-bold text-white whitespace-nowrap">
                      {a.symbol}
                    </td>

                    {/* Signal BUY/SELL */}
                    <td className="px-4 py-3 text-center">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-bold ${isBuy
                        ? "bg-emerald-950/60 text-emerald-300 border border-emerald-800"
                        : "bg-red-950/60 text-red-300 border border-red-800"
                        }`}>
                        {a.signal}
                      </span>
                    </td>

                    {/* CALL / PUT */}
                    <td className="px-4 py-3 text-center">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-bold ${isBuy
                        ? "bg-blue-950/60 text-blue-300 border border-blue-800"
                        : "bg-orange-950/60 text-orange-300 border border-orange-800"
                        }`}>
                        {optionType}
                      </span>
                    </td>

                    {/* Strategy */}
                    <td className="px-4 py-3 text-gray-300 text-xs whitespace-nowrap">
                      {a.strategy}
                    </td>

                    {/* Timeframe */}
                    <td className="px-4 py-3 text-center">
                      <span className="px-2 py-1 bg-gray-800 rounded text-xs text-gray-400">
                        {a.timeframe}
                      </span>
                    </td>

                    {/* Entry */}
                    <td className="px-4 py-3 text-right font-mono text-white">
                      {a.entry_price > 0 ? `₹${a.entry_price.toFixed(2)}` : "—"}
                    </td>

                    {/* Stop Loss */}
                    <td className="px-4 py-3 text-right font-mono text-red-400">
                      {a.stop_loss > 0 ? `₹${a.stop_loss.toFixed(2)}` : "—"}
                    </td>

                    {/* Target */}
                    <td className="px-4 py-3 text-right font-mono text-emerald-400">
                      {a.target_price > 0 ? `₹${a.target_price.toFixed(2)}` : "—"}
                    </td>

                    {/* R:R */}
                    <td className={`px-4 py-3 text-right font-mono font-bold ${a.risk_reward >= 2 ? "text-emerald-400" : "text-yellow-400"}`}>
                      {a.risk_reward > 0 ? `1:${a.risk_reward}` : "—"}
                    </td>

                    {/* RSI */}
                    <td className={`px-4 py-3 text-right font-mono text-xs ${rsiVal
                      ? rsiVal >= 70 ? "text-red-400" : rsiVal <= 30 ? "text-emerald-400" : "text-gray-400"
                      : "text-gray-600"
                      }`}>
                      {rsiVal ? rsiVal.toFixed(1) : "—"}
                    </td>

                    {/* Trend */}
                    <td className="px-4 py-3">
                      <span className={`text-xs ${a.trend === "UPTREND" ? "text-emerald-400"
                        : a.trend === "DOWNTREND" ? "text-red-400"
                          : "text-gray-600"
                        }`}>
                        {a.trend ?? "—"}
                      </span>
                    </td>

                    {/* Actions */}
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => executePaperTrade(a)}
                          className="text-xs px-3 py-1 bg-blue-600 hover:bg-blue-500 rounded text-white font-medium transition"
                        >
                          🕹️ Trade
                        </button>
                        <button
                          onClick={() => deleteAlert(a.id)}
                          className="text-gray-600 hover:text-red-400 p-1"
                          title="Delete Alert"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────

export default function ScannerPage() {
  const [tab, setTab] = useState<"auto" | "single" | "alerts">(
    () => (loadScannerState()?.tab as "auto" | "single" | "alerts") ?? "auto"
  );

  // Persist active tab so it survives navigation
  const handleTabChange = (t: "auto" | "single" | "alerts") => {
    setTab(t);
    saveScannerState({ tab: t });
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-3">
            <Zap className="w-6 h-6 text-yellow-400" /> Signal Scanner
          </h1>
          <p className="text-gray-400 mt-1 text-sm">
            Auto-scan entire market lists, or search any individual NSE stock —
            powered by Upstox
          </p>
        </div>
        <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-lg p-1">
          <button
            onClick={() => handleTabChange("auto")}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${tab === "auto" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white"}`}
          >
            🔍 Auto Scan
          </button>
          <button
            onClick={() => handleTabChange("single")}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${tab === "single" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white"}`}
          >
            📊 Single Stock
          </button>
          <button
            onClick={() => handleTabChange("alerts")}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center gap-1.5 ${tab === "alerts" ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white"}`}
          >
            <Bell className="w-4 h-4" /> Alerts
          </button>
        </div>
      </div>

      {tab === "auto" && <AutoScanner />}
      {tab === "single" && <SingleScanner />}
      {tab === "alerts" && <AlertsHistory />}
    </div>
  );
}
