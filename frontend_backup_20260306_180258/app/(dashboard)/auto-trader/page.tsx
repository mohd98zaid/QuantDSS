"use client";

import { useEffect, useState, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

interface Config {
  enabled: boolean;
  mode: string;
  sizing_mode: string; // "capital" or "quantity"
  qty_per_trade: number;
  capital_per_trade: number; // ₹ amount
  max_open_positions: number;
  strategy: string;
  timeframe: string;
  watchlist: string[];
  scan_interval_minutes: number;
}

interface LogEntry {
  id: number;
  timestamp: string;
  symbol: string | null;
  signal: string | null;
  action: string;
  reason: string | null;
  entry_price: number | null;
  stop_loss: number | null;
  target_price: number | null;
  risk_reward: number | null;
  rsi: number | null;
  trend: string | null;
  strategy: string | null;
  timeframe: string | null;
  trade_id: number | null;
}

function getToken() {
  return typeof window !== "undefined"
    ? localStorage.getItem("quantdss_token")
    : null;
}
function authHeaders(): Record<string, string> {
  const token = getToken();
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (token) h["Authorization"] = `Bearer ${token}`;
  return h;
}

export default function AutoTraderPage() {
  const [config, setConfig] = useState<Config>({
    enabled: false,
    mode: "paper",
    sizing_mode: "capital",
    qty_per_trade: 1,
    capital_per_trade: 10000,
    max_open_positions: 3,
    strategy: "ema_crossover",
    timeframe: "5min",
    watchlist: [],
    scan_interval_minutes: 5,
  });
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState(false);
  const [saving, setSaving] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [cfgRes, logRes] = await Promise.all([
        fetch(`${API_BASE}/v1/auto-trader/config`, { headers: authHeaders() }),
        fetch(`${API_BASE}/v1/auto-trader/log?limit=50`, {
          headers: authHeaders(),
        }),
      ]);
      if (cfgRes.ok) setConfig(await cfgRes.json());
      if (logRes.ok) setLogs(await logRes.json());
    } catch (e) {
      console.error("AutoTrader fetch error", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const iv = setInterval(fetchAll, 15_000);
    return () => clearInterval(iv);
  }, [fetchAll]);

  const saveConfig = async (updates: Partial<Config>) => {
    const next = { ...config, ...updates };
    setConfig(next);
    setSaving(true);
    try {
      const res = await fetch(`${API_BASE}/v1/auto-trader/config`, {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify(next),
      });
      if (res.ok) setConfig(await res.json());
    } catch (e) {
      console.error("Save failed", e);
    } finally {
      setSaving(false);
    }
  };

  const toggleEnabled = async () => {
    setToggling(true);
    await saveConfig({ enabled: !config.enabled });
    setToggling(false);
  };

  const resetLogs = async () => {
    if (
      !confirm(
        "Are you sure you want to delete all auto-trader logs? This action cannot be undone.",
      )
    )
      return;

    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/v1/auto-trader/data`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (res.ok) {
        await fetchAll();
      } else {
        alert("Failed to reset logs");
      }
    } catch (e) {
      console.error("Failed to reset logs", e);
    } finally {
      setLoading(false);
    }
  };

  const exportLogs = async () => {
    try {
      const res = await fetch(`${API_BASE}/v1/auto-trader/export`, {
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error("Export failed");

      const blob = await res.blob();
      const downloadUrl = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = downloadUrl;
      a.download = "auto_trader_logs.csv";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(downloadUrl);
    } catch (e) {
      console.error("Failed to export logs", e);
      alert("Failed to export logs to CSV");
    }
  };

  if (loading)
    return (
      <div className="p-8 text-gray-400 text-center">Loading Auto Trader…</div>
    );

  const actionColor = (action: string) => {
    if (action === "OPEN")
      return "text-emerald-400 bg-emerald-950/50 border-emerald-800";
    if (action === "CLOSE")
      return "text-blue-400 bg-blue-950/50 border-blue-800";
    if (action === "ERROR") return "text-red-400 bg-red-950/50 border-red-800";
    return "text-yellow-400 bg-yellow-950/50 border-yellow-800";
  };

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      {/* ── Hero Toggle ─────────────────────────────────────────────── */}
      <div
        className={`rounded-2xl border-2 p-6 flex items-center justify-between transition-all ${
          config.enabled
            ? "bg-emerald-950/30 border-emerald-700/60"
            : "bg-gray-900/80 border-gray-800"
        }`}
      >
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            🤖 Auto Trader
            {config.enabled && (
              <span className="text-xs font-normal px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-700 ml-1 animate-pulse">
                LIVE
              </span>
            )}
          </h1>
          <p className="text-sm text-gray-400 mt-1">
            {config.enabled
              ? "Auto-trader is ON — picks scanner signals and trades automatically (entry + exit)."
              : "Auto-trader is OFF — toggle to enable automatic trade execution from scanner signals."}
          </p>
        </div>

        {/* Big pill toggle */}
        <button
          onClick={toggleEnabled}
          disabled={toggling}
          className={`relative flex items-center gap-3 px-6 py-3 rounded-xl font-bold text-base transition-all duration-300 shadow-lg ${
            config.enabled
              ? "bg-emerald-500 hover:bg-emerald-400 text-white shadow-emerald-900/50"
              : "bg-gray-700 hover:bg-gray-600 text-gray-300 shadow-black/30"
          } ${toggling ? "opacity-60 cursor-not-allowed" : ""}`}
        >
          {/* Toggle track */}
          <span
            className={`w-12 h-6 rounded-full relative transition-colors duration-300 ${
              config.enabled ? "bg-white/30" : "bg-gray-900/50"
            }`}
          >
            <span
              className={`absolute top-0.5 w-5 h-5 rounded-full transition-all duration-300 shadow ${
                config.enabled ? "left-6 bg-white" : "left-0.5 bg-gray-500"
              }`}
            />
          </span>
          {toggling ? "…" : config.enabled ? "ON" : "OFF"}
        </button>
      </div>

      {/* ── Trade Settings ─────────────────────────────────────────── */}
      <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-5 space-y-5">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
          Trade Settings
        </h2>

        {/* Sizing Mode Toggle */}
        <div>
          <label className="text-xs text-gray-500 uppercase tracking-wider mb-2 block">
            Position Sizing
          </label>
          <div className="flex gap-2">
            <button
              onClick={() => saveConfig({ sizing_mode: "capital" })}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
                config.sizing_mode === "capital"
                  ? "bg-blue-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:text-white border border-gray-700"
              }`}
            >
              💰 Capital (₹)
            </button>
            <button
              onClick={() => saveConfig({ sizing_mode: "quantity" })}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
                config.sizing_mode === "quantity"
                  ? "bg-blue-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:text-white border border-gray-700"
              }`}
            >
              📦 Fixed Quantity
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {/* Capital or Qty Input */}
          {config.sizing_mode === "capital" ? (
            <div>
              <label className="text-xs text-gray-500 uppercase tracking-wider mb-1 block">
                Max Capital per Trade (₹)
              </label>
              <input
                type="number"
                min={100}
                max={1000000}
                step={100}
                value={config.capital_per_trade}
                onChange={(e) =>
                  setConfig((c) => ({
                    ...c,
                    capital_per_trade: parseFloat(e.target.value) || 100,
                  }))
                }
                onBlur={() => saveConfig({})}
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white"
              />
              <p className="text-xs text-gray-600 mt-1">
                Auto-calculates qty:{" "}
                <span className="text-gray-400">
                  qty = ⌊ ₹{config.capital_per_trade.toLocaleString()} ÷ stock
                  price ⌋
                </span>
              </p>
            </div>
          ) : (
            <div>
              <label className="text-xs text-gray-500 uppercase tracking-wider mb-1 block">
                Qty per Trade
              </label>
              <input
                type="number"
                min={1}
                max={100}
                value={config.qty_per_trade}
                onChange={(e) =>
                  setConfig((c) => ({
                    ...c,
                    qty_per_trade: parseInt(e.target.value) || 1,
                  }))
                }
                onBlur={() => saveConfig({})}
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white"
              />
              <p className="text-xs text-gray-600 mt-1">
                Fixed number of shares per trade
              </p>
            </div>
          )}

          {/* Max Open Positions */}
          <div>
            <label className="text-xs text-gray-500 uppercase tracking-wider mb-1 block">
              Max Open Positions
            </label>
            <input
              type="number"
              min={1}
              max={20}
              value={config.max_open_positions}
              onChange={(e) =>
                setConfig((c) => ({
                  ...c,
                  max_open_positions: parseInt(e.target.value) || 1,
                }))
              }
              onBlur={() => saveConfig({})}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white"
            />
          </div>
        </div>

        <div className="flex items-center justify-between pt-1 text-xs text-gray-500">
          <span>
            Reacts to scanner BUY/SELL signals · Auto-exits on reverse signal
          </span>
          <span
            className={`px-2 py-0.5 rounded-full border ${
              saving
                ? "text-yellow-400 border-yellow-800 bg-yellow-950/30"
                : "text-gray-600 border-gray-800"
            }`}
          >
            {saving ? "saving…" : "saved"}
          </span>
        </div>
      </div>

      {/* ── Activity Log ─────────────────────────────────────────────── */}
      <div className="bg-gray-900/80 border border-gray-800 rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-800 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <h2 className="font-semibold text-white">Activity Log</h2>
            <span className="text-xs text-gray-500">Refreshes every 15s</span>
          </div>
          <div className="flex gap-2">
            <button
              onClick={exportLogs}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-700 rounded-lg transition"
              title="Download full log history as CSV"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              Export CSV
            </button>
            <button
              onClick={resetLogs}
              className="text-xs px-3 py-1.5 bg-red-950/40 hover:bg-red-900/60 text-red-400 border border-red-900/50 rounded-lg transition"
            >
              Reset Logs
            </button>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs uppercase text-gray-500 border-b border-gray-800 text-left">
                <th className="px-3 py-3">Time</th>
                <th className="px-3 py-3">Symbol</th>
                <th className="px-3 py-3">Signal</th>
                <th className="px-3 py-3">Action</th>
                <th className="px-3 py-3">Entry</th>
                <th className="px-3 py-3">SL</th>
                <th className="px-3 py-3">Target</th>
                <th className="px-3 py-3">R:R</th>
                <th className="px-3 py-3">RSI</th>
                <th className="px-3 py-3">Trend</th>
                <th className="px-3 py-3">Strategy</th>
                <th className="px-3 py-3">TF</th>
                <th className="px-3 py-3">Reason</th>
              </tr>
            </thead>
            <tbody>
              {logs.length === 0 ? (
                <tr>
                  <td
                    colSpan={13}
                    className="px-4 py-10 text-center text-gray-600 italic"
                  >
                    No activity yet — enable the auto-trader, then run a scan to
                    generate signals.
                  </td>
                </tr>
              ) : (
                logs.map((log) => (
                  <tr
                    key={log.id}
                    className="border-b border-gray-800/50 hover:bg-gray-800/30 transition"
                  >
                    <td className="px-3 py-2.5 text-xs text-gray-400 whitespace-nowrap font-mono">
                      {new Date(log.timestamp).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                        second: "2-digit",
                      })}
                    </td>
                    <td className="px-3 py-2.5 font-bold text-white whitespace-nowrap">
                      {log.symbol ?? "—"}
                    </td>
                    <td className="px-3 py-2.5">
                      {log.signal ? (
                        <span
                          className={`text-xs font-bold px-2 py-0.5 rounded-md border ${
                            log.signal === "BUY"
                              ? "bg-emerald-950/50 text-emerald-300 border-emerald-800"
                              : "bg-red-950/50 text-red-300 border-red-800"
                          }`}
                        >
                          {log.signal}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-2.5">
                      <span
                        className={`text-xs font-bold px-2 py-0.5 rounded-md border ${actionColor(log.action)}`}
                      >
                        {log.action}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 font-mono text-gray-200 whitespace-nowrap">
                      {log.entry_price ? `₹${log.entry_price.toFixed(2)}` : "—"}
                    </td>
                    <td className="px-3 py-2.5 font-mono text-red-400 whitespace-nowrap">
                      {log.stop_loss ? `₹${log.stop_loss.toFixed(2)}` : "—"}
                    </td>
                    <td className="px-3 py-2.5 font-mono text-emerald-400 whitespace-nowrap">
                      {log.target_price
                        ? `₹${log.target_price.toFixed(2)}`
                        : "—"}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      {log.risk_reward ? (
                        <span
                          className={`text-xs font-semibold px-1.5 py-0.5 rounded ${
                            log.risk_reward >= 2
                              ? "text-emerald-300"
                              : log.risk_reward >= 1
                                ? "text-yellow-300"
                                : "text-red-400"
                          }`}
                        >
                          {log.risk_reward.toFixed(1)}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      {log.rsi ? (
                        <span
                          className={`text-xs font-mono ${
                            log.rsi >= 70
                              ? "text-red-400"
                              : log.rsi <= 30
                                ? "text-emerald-400"
                                : "text-gray-400"
                          }`}
                        >
                          {log.rsi.toFixed(1)}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-2.5">
                      {log.trend ? (
                        <span
                          className={`text-xs font-semibold ${
                            log.trend === "UPTREND"
                              ? "text-emerald-400"
                              : log.trend === "DOWNTREND"
                                ? "text-red-400"
                                : "text-gray-400"
                          }`}
                        >
                          {log.trend === "UPTREND"
                            ? "↑"
                            : log.trend === "DOWNTREND"
                              ? "↓"
                              : ""}{" "}
                          {log.trend}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-xs text-gray-400 whitespace-nowrap">
                      {log.strategy
                        ? log.strategy
                            .replace(/_/g, " ")
                            .replace(/\b\w/g, (l) => l.toUpperCase())
                        : "—"}
                    </td>
                    <td className="px-3 py-2.5 text-xs text-gray-500">
                      {log.timeframe ?? "—"}
                    </td>
                    <td className="px-3 py-2.5 text-xs text-gray-500 max-w-xs truncate">
                      {log.reason ?? "—"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
