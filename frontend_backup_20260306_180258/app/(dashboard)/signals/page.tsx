"use client";

import { useEffect, useState } from "react";
import { getSignals } from "@/lib/api";

interface Signal {
  id: number;
  timestamp: string;
  signal_type: string;
  symbol?: string;
  strategy?: string;
  entry_price?: number;
  stop_loss?: number;
  target_price?: number;
  risk_status: string;
  block_reason?: string;
  quantity?: number;
  risk_amount?: number;
  risk_reward?: number;
  atr_pct?: number;
}

export default function SignalsPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [filter, setFilter] = useState<string>("ALL");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadSignals();
  }, [filter]);

  async function loadSignals() {
    try {
      setLoading(true);
      const params: Record<string, string> = { page_size: "50" };
      if (filter !== "ALL") params.status = filter;
      const data = await getSignals(params);
      setSignals(data.signals || []);
    } catch (e) {
      console.error("Failed to load signals", e);
    } finally {
      setLoading(false);
    }
  }

  const exportCsv = async () => {
    try {
      const token =
        typeof window !== "undefined"
          ? localStorage.getItem("quantdss_token")
          : null;
      const headers: Record<string, string> = {};
      if (token) headers["Authorization"] = `Bearer ${token}`;

      let url = `${process.env.NEXT_PUBLIC_API_URL || "/api"}/v1/signals/export`;
      if (filter !== "ALL") {
        url += `?status=${filter}`;
      }

      const response = await fetch(url, { headers });
      if (!response.ok) throw new Error("Export failed");

      const blob = await response.blob();
      const downloadUrl = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = downloadUrl;
      a.download = `signals_${filter.toLowerCase()}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(downloadUrl);
    } catch (e) {
      console.error("Failed to export CSV", e);
      alert("Failed to export CSV");
    }
  };

  const filters = ["ALL", "APPROVED", "BLOCKED", "SKIPPED"];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Signal Feed</h1>
        <p className="text-gray-400 mt-1">
          All generated signals with risk decisions
        </p>
      </div>

      {/* Filter Tabs & Export */}
      <div className="flex items-center justify-between">
        <div className="flex gap-2">
          {filters.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                filter === f
                  ? "bg-blue-600 text-white"
                  : "bg-surface-900 text-gray-400 hover:text-white border border-gray-800"
              }`}
            >
              {f}
            </button>
          ))}
        </div>

        <button
          onClick={exportCsv}
          className="flex items-center gap-2 px-4 py-2 bg-surface-800 hover:bg-surface-700 text-gray-300 hover:text-white border border-gray-700 rounded-lg text-sm font-medium transition-colors"
          title="Export displayed signals to CSV"
        >
          <svg
            className="w-4 h-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
            />
          </svg>
          Export CSV
        </button>
      </div>

      {/* Signal Table */}
      <div className="bg-surface-900 border border-gray-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-gray-400 text-left">
              <th className="p-4">Time</th>
              <th className="p-4">Type</th>
              <th className="p-4">Symbol</th>
              <th className="p-4">Entry</th>
              <th className="p-4">SL</th>
              <th className="p-4">Target</th>
              <th className="p-4">Qty</th>
              <th className="p-4">R:R</th>
              <th className="p-4">Status</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={9} className="p-8 text-center text-gray-500">
                  Loading...
                </td>
              </tr>
            ) : signals.length === 0 ? (
              <tr>
                <td colSpan={9} className="p-8 text-center text-gray-500">
                  No signals yet. Signals will appear here during market hours.
                </td>
              </tr>
            ) : (
              signals.map((sig) => (
                <tr
                  key={sig.id}
                  className="border-b border-gray-800/50 hover:bg-surface-800/50"
                >
                  <td className="p-4 text-gray-400 text-xs font-mono-nums">
                    {sig.timestamp
                      ? new Date(sig.timestamp).toLocaleTimeString()
                      : "—"}
                  </td>
                  <td
                    className={`p-4 font-bold ${
                      sig.signal_type === "BUY"
                        ? "text-emerald-400"
                        : "text-red-400"
                    }`}
                  >
                    {sig.signal_type}
                  </td>
                  <td className="p-4 font-medium">{sig.symbol || "—"}</td>
                  <td className="p-4 font-mono-nums">
                    ₹{sig.entry_price?.toFixed(2) || "—"}
                  </td>
                  <td className="p-4 font-mono-nums text-red-400">
                    ₹{sig.stop_loss?.toFixed(2) || "—"}
                  </td>
                  <td className="p-4 font-mono-nums text-emerald-400">
                    ₹{sig.target_price?.toFixed(2) || "—"}
                  </td>
                  <td className="p-4 font-mono-nums">{sig.quantity || "—"}</td>
                  <td className="p-4 font-mono-nums">
                    {sig.risk_reward?.toFixed(1) || "—"}
                  </td>
                  <td className="p-4">
                    <span
                      className={`text-xs px-2 py-1 rounded ${
                        sig.risk_status === "APPROVED"
                          ? "bg-emerald-900/50 text-emerald-300"
                          : sig.risk_status === "BLOCKED"
                            ? "bg-red-900/50 text-red-300"
                            : "bg-yellow-900/50 text-yellow-300"
                      }`}
                    >
                      {sig.risk_status}
                    </span>
                    {sig.block_reason && (
                      <p className="text-xs text-gray-500 mt-1">
                        {sig.block_reason}
                      </p>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
