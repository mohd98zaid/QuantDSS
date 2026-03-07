"use client";

import { useEffect, useState } from "react";
import {
  getRiskConfig,
  updateRiskConfig,
  getSymbols,
  getBrokerHealth,
  addSymbol,
  deleteSymbol,
} from "@/lib/api";
import { Trash2, Plus, RefreshCw, Layers, CheckCheck } from "lucide-react";

interface RiskConfig {
  risk_per_trade_pct: number;
  max_daily_loss_inr: number;
  max_daily_loss_pct: number;
  max_account_drawdown_pct: number;
  cooldown_minutes: number;
  min_atr_pct: number;
  max_atr_pct: number;
  max_position_pct: number;
  max_concurrent_positions: number;
}

// ── Stock Lists ───────────────────────────────────────────────────────────────
const STOCK_LISTS = [
  {
    id: "nifty50",
    label: "Nifty 50",
    color: "blue",
    icon: "🏆",
    stocks: [
      "ADANIENT",
      "ADANIPORTS",
      "APOLLOHOSP",
      "ASIANPAINT",
      "AXISBANK",
      "BAJAJ-AUTO",
      "BAJAJFINSV",
      "BAJFINANCE",
      "BHARTIARTL",
      "BPCL",
      "BRITANNIA",
      "CIPLA",
      "COALINDIA",
      "DIVISLAB",
      "DRREDDY",
      "EICHERMOT",
      "GRASIM",
      "HCLTECH",
      "HDFCBANK",
      "HDFCLIFE",
      "HEROMOTOCO",
      "HINDALCO",
      "HINDUNILVR",
      "ICICIBANK",
      "ITC",
      "INDUSINDBK",
      "INFY",
      "JSWSTEEL",
      "KOTAKBANK",
      "LT",
      "M&M",
      "MARUTI",
      "NTPC",
      "NESTLEIND",
      "ONGC",
      "POWERGRID",
      "RELIANCE",
      "SBILIFE",
      "SBIN",
      "SUNPHARMA",
      "TCS",
      "TATACONSUM",
      "TATAMOTORS",
      "TATASTEEL",
      "TECHM",
      "TITAN",
      "ULTRACEMCO",
      "UPL",
      "WIPRO",
      "GAIL",
    ],
  },
  {
    id: "banknifty",
    label: "Bank Nifty",
    color: "emerald",
    icon: "🏦",
    stocks: [
      "AXISBANK",
      "BANDHANBNK",
      "FEDERALBNK",
      "HDFCBANK",
      "ICICIBANK",
      "IDFCFIRSTB",
      "INDUSINDBK",
      "KOTAKBANK",
      "PNB",
      "SBIN",
      "AUBANK",
      "BANKBARODA",
    ],
  },
  {
    id: "niftyit",
    label: "Nifty IT",
    color: "purple",
    icon: "💻",
    stocks: [
      "COFORGE",
      "HCLTECH",
      "INFY",
      "LTIMindtree",
      "LTTS",
      "MPHASIS",
      "PERSISTENT",
      "TCS",
      "TECHM",
      "WIPRO",
    ],
  },
  {
    id: "foactives",
    label: "F&O Actives",
    color: "orange",
    icon: "⚡",
    stocks: [
      "RELIANCE",
      "INFY",
      "TCS",
      "HDFCBANK",
      "ICICIBANK",
      "AXISBANK",
      "BHARTIARTL",
      "SBIN",
      "TATAMOTORS",
      "WIPRO",
      "BAJFINANCE",
      "KOTAKBANK",
      "M&M",
      "MARUTI",
      "LT",
      "SUNPHARMA",
      "TATASTEEL",
      "JSWSTEEL",
      "NTPC",
      "POWERGRID",
    ],
  },
  {
    id: "psu",
    label: "PSU",
    color: "yellow",
    icon: "🏛️",
    stocks: [
      "SBIN",
      "BANKBARODA",
      "PNB",
      "CANARABANK",
      "UNIONBANK",
      "BPCL",
      "ONGC",
      "GAIL",
      "IOC",
      "COALINDIA",
      "NTPC",
      "POWERGRID",
      "NHPC",
      "SJVN",
      "RECLTD",
      "PFC",
      "IRFC",
      "BHEL",
      "HAL",
      "BEL",
    ],
  },
  {
    id: "midcap",
    label: "Midcap",
    color: "pink",
    icon: "📈",
    stocks: [
      "ABCAPITAL",
      "ASTRAL",
      "AUROPHARMA",
      "BALKRISIND",
      "CANFINHOME",
      "CROMPTON",
      "DEEPAKNITRITE",
      "GLENMARK",
      "HAPPSTMNDS",
      "HINDPETRO",
      "IPCALAB",
      "MAXHEALTH",
      "METROPOLIS",
      "MRF",
      "NAUKRI",
    ],
  },
];

const colorMap: Record<
  string,
  { bg: string; border: string; badge: string; text: string; btn: string }
> = {
  blue: {
    bg: "bg-blue-950/30",
    border: "border-blue-800/60",
    badge: "bg-blue-600/20 text-blue-300",
    text: "text-blue-400",
    btn: "bg-blue-600 hover:bg-blue-500",
  },
  emerald: {
    bg: "bg-emerald-950/30",
    border: "border-emerald-800/60",
    badge: "bg-emerald-600/20 text-emerald-300",
    text: "text-emerald-400",
    btn: "bg-emerald-600 hover:bg-emerald-500",
  },
  purple: {
    bg: "bg-purple-950/30",
    border: "border-purple-800/60",
    badge: "bg-purple-600/20 text-purple-300",
    text: "text-purple-400",
    btn: "bg-purple-600 hover:bg-purple-500",
  },
  orange: {
    bg: "bg-orange-950/30",
    border: "border-orange-800/60",
    badge: "bg-orange-600/20 text-orange-300",
    text: "text-orange-400",
    btn: "bg-orange-600 hover:bg-orange-500",
  },
  yellow: {
    bg: "bg-yellow-950/30",
    border: "border-yellow-800/60",
    badge: "bg-yellow-600/20 text-yellow-300",
    text: "text-yellow-400",
    btn: "bg-yellow-600 hover:bg-yellow-500",
  },
  pink: {
    bg: "bg-pink-950/30",
    border: "border-pink-800/60",
    badge: "bg-pink-600/20 text-pink-300",
    text: "text-pink-400",
    btn: "bg-pink-600 hover:bg-pink-500",
  },
};

export default function SettingsPage() {
  const [config, setConfig] = useState<RiskConfig | null>(null);
  const [symbols, setSymbols] = useState<any[]>([]);
  const [brokerHealth, setBrokerHealth] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  // Upstox token hot-update (FIX #10)
  const [upstoxToken, setUpstoxToken] = useState("");
  const [tokenSaving, setTokenSaving] = useState(false);
  const [tokenMsg, setTokenMsg] = useState<string | null>(null);
  const [tokenError, setTokenError] = useState<string | null>(null);

  // Add-symbol form
  const [newSymbol, setNewSymbol] = useState("");
  const [newExchange, setNewExchange] = useState("NSE");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [addSuccess, setAddSuccess] = useState<string | null>(null);

  // Bulk add (single list)
  const [bulkLoading, setBulkLoading] = useState<string | null>(null);
  const [bulkMsg, setBulkMsg] = useState<string | null>(null);

  // Multi-select
  const [selectedLists, setSelectedLists] = useState<Set<string>>(new Set());
  const [multiAdding, setMultiAdding] = useState(false);

  // Delete
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const loadSymbols = async () => {
    try {
      const list = await getSymbols();
      setSymbols(list);
    } catch {}
  };

  useEffect(() => {
    getRiskConfig()
      .then(setConfig)
      .catch(() => {});
    loadSymbols();
    getBrokerHealth()
      .then(setBrokerHealth)
      .catch(() => {});
  }, []);

  async function handleSave() {
    if (!config) return;
    setSaving(true);
    try {
      await updateRiskConfig(config);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      console.error("Failed to save", e);
    } finally {
      setSaving(false);
    }
  }

  // FIX #10: hot-update Upstox token without Docker restart
  async function handleSaveToken() {
    if (!upstoxToken.trim()) return;
    setTokenSaving(true);
    setTokenMsg(null);
    setTokenError(null);
    try {
      const res = await fetch("/api/v1/health/broker/token", {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          // FIX #2: api.ts stores JWT under "quantdss_token", not "token"
          Authorization: `Bearer ${localStorage.getItem("quantdss_token") ?? ""}`,
        },
        body: JSON.stringify({ token: upstoxToken.trim() }),
      });
      if (!res.ok) throw new Error(await res.text());
      setTokenMsg("✅ Token saved. Recovery monitor will reconnect Upstox within 60 s.");
      setUpstoxToken("");
    } catch (err: any) {
      setTokenError(err.message || "Failed to save token");
    } finally {
      setTokenSaving(false);
    }
  }

  async function handleAddSymbol(e: React.FormEvent) {
    e.preventDefault();
    if (!newSymbol.trim()) return;
    setAdding(true);
    setAddError(null);
    setAddSuccess(null);
    try {
      await addSymbol(newSymbol.trim().toUpperCase(), newExchange);
      setAddSuccess(`✅ ${newSymbol.toUpperCase()} added`);
      setNewSymbol("");
      await loadSymbols();
      setTimeout(() => setAddSuccess(null), 3000);
    } catch (err: any) {
      setAddError(err.message || "Failed to add symbol");
    } finally {
      setAdding(false);
    }
  }

  async function handleDelete(id: number, sym: string) {
    setDeletingId(id);
    try {
      await deleteSymbol(id);
      await loadSymbols();
    } catch (e: any) {
      alert(`Failed to remove ${sym}: ${e.message}`);
    } finally {
      setDeletingId(null);
    }
  }

  async function handleBulkAdd(listId: string, stocks: string[]) {
    setBulkLoading(listId);
    setBulkMsg(null);
    const existing = new Set(symbols.map((s: any) => s.trading_symbol));
    const toAdd = stocks.filter((s) => !existing.has(s));
    if (toAdd.length === 0) {
      setBulkMsg("All stocks already in watchlist!");
      setBulkLoading(null);
      setTimeout(() => setBulkMsg(null), 3000);
      return;
    }
    let added = 0;
    for (const sym of toAdd) {
      try { await addSymbol(sym, "NSE"); added++; } catch {}
    }
    await loadSymbols();
    setBulkMsg(`✅ Added ${added} stocks`);
    setBulkLoading(null);
    setTimeout(() => setBulkMsg(null), 4000);
  }

  async function handleBulkRemove(listId: string, stocks: string[]) {
    setBulkLoading(listId);
    setBulkMsg(null);
    const stockSet = new Set(stocks);
    // find IDs of watchlist entries that belong to this list
    const toDelete = symbols.filter((s: any) => stockSet.has(s.trading_symbol));
    let removed = 0;
    for (const sym of toDelete) {
      try { await deleteSymbol(sym.id); removed++; } catch {}
    }
    await loadSymbols();
    setBulkMsg(`🗑️ Removed ${removed} stocks from ${STOCK_LISTS.find(l => l.id === listId)?.label}`);
    setBulkLoading(null);
    setTimeout(() => setBulkMsg(null), 4000);
  }

  function toggleListSelect(listId: string) {
    setSelectedLists((prev) => {
      const next = new Set(prev);
      if (next.has(listId)) next.delete(listId);
      else next.add(listId);
      return next;
    });
  }

  async function handleAddSelected() {
    if (selectedLists.size === 0) return;
    setMultiAdding(true);
    setBulkMsg(null);
    const existing = new Set(symbols.map((s: any) => s.trading_symbol));
    const allStocks = STOCK_LISTS.filter((l) =>
      selectedLists.has(l.id),
    ).flatMap((l) => l.stocks);
    const unique = Array.from(new Set(allStocks)).filter((s) => !existing.has(s));
    let added = 0;
    for (const sym of unique) {
      try {
        await addSymbol(sym, "NSE");
        added++;
      } catch {}
    }
    await loadSymbols();
    const skipped = unique.length - added;
    setBulkMsg(
      `✅ Added ${added} stocks from ${selectedLists.size} categories${skipped > 0 ? ` (${skipped} skipped)` : ""}`,
    );
    setSelectedLists(new Set());
    setMultiAdding(false);
    setTimeout(() => setBulkMsg(null), 4000);
  }

  const updateField = (field: keyof RiskConfig, value: number) => {
    setConfig((prev) => (prev ? { ...prev, [field]: value } : null));
  };

  const existingSet = new Set(symbols.map((s: any) => s.trading_symbol));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-gray-400 mt-1">
          Configure risk parameters, watchlist, and broker connections
        </p>
      </div>

      {/* ── Stock Lists ── */}
      <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-6">
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-3">
            <Layers className="w-5 h-5 text-blue-400" />
            <div>
              <h2 className="text-lg font-semibold">Stock Lists</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                Select one or more indices, then add them all at once
              </p>
            </div>
          </div>
          {selectedLists.size > 0 && (
            <div className="flex items-center gap-3">
              <span className="text-xs text-gray-400">
                {selectedLists.size} categor
                {selectedLists.size === 1 ? "y" : "ies"} selected
              </span>
              <button
                onClick={() => setSelectedLists(new Set())}
                className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
              >
                Clear
              </button>
              <button
                onClick={handleAddSelected}
                disabled={multiAdding}
                className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors"
              >
                {multiAdding ? (
                  <>
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    Adding…
                  </>
                ) : (
                  <>
                    <Plus className="w-3.5 h-3.5" />
                    Add Selected
                  </>
                )}
              </button>
            </div>
          )}
        </div>

        {bulkMsg && (
          <div className="mb-4 px-4 py-2.5 rounded-lg bg-emerald-950/50 border border-emerald-800 text-emerald-300 text-sm">
            {bulkMsg}
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {STOCK_LISTS.map((list) => {
            const c = colorMap[list.color];
            const alreadyAdded = list.stocks.filter((s) =>
              existingSet.has(s),
            ).length;
            const isLoading = bulkLoading === list.id;
            const isSelected = selectedLists.has(list.id);
            return (
              <div
                key={list.id}
                onClick={() => toggleListSelect(list.id)}
                className={`${c.bg} border-2 rounded-xl p-4 flex flex-col gap-3 transition-all cursor-pointer hover:scale-[1.01] ${
                  isSelected
                    ? `${c.border} ring-2 ring-offset-1 ring-offset-gray-900 ring-blue-500`
                    : "border-gray-800/60"
                }`}
              >
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-2">
                    {/* Checkbox */}
                    <div
                      className={`w-4 h-4 rounded border-2 flex items-center justify-center flex-shrink-0 transition-colors ${
                        isSelected
                          ? "bg-blue-600 border-blue-600"
                          : "border-gray-600 bg-transparent"
                      }`}
                    >
                      {isSelected && (
                        <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 10 10">
                          <path d="M1.5 5l2.5 2.5 4.5-4.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      )}
                    </div>
                    <span className="text-xl">{list.icon}</span>
                    <div>
                      <p className={`font-semibold text-sm ${c.text}`}>
                        {list.label}
                        <span className="text-gray-500 font-normal ml-1">({list.stocks.length})</span>
                      </p>
                    </div>
                  </div>
                  {alreadyAdded > 0 && (
                    <span className={`text-xs px-2 py-0.5 rounded-full ${c.badge} font-medium`}>
                      {alreadyAdded}/{list.stocks.length} added
                    </span>
                  )}
                </div>

                {/* Stocks chip preview */}
                <div className="flex flex-wrap gap-1">
                  {list.stocks.slice(0, 8).map((s) => (
                    <span
                      key={s}
                      className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                        existingSet.has(s)
                          ? "bg-emerald-900/40 text-emerald-400"
                          : "bg-gray-800 text-gray-400"
                      }`}
                    >{s}</span>
                  ))}
                  {list.stocks.length > 8 && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-500">
                      +{list.stocks.length - 8} more
                    </span>
                  )}
                </div>

                {/* Toggle button: Add ↔ Remove */}
                {alreadyAdded === list.stocks.length ? (
                  // ALL added → show red Remove All button
                  <button
                    onClick={(e) => { e.stopPropagation(); handleBulkRemove(list.id, list.stocks); }}
                    disabled={isLoading || multiAdding}
                    className="flex items-center justify-center gap-2 w-full py-2 rounded-lg text-xs font-semibold text-white transition-all disabled:opacity-50 disabled:cursor-not-allowed bg-red-700 hover:bg-red-600"
                  >
                    {isLoading ? (
                      <><RefreshCw className="w-3.5 h-3.5 animate-spin" /> Removing…</>
                    ) : (
                      <><Trash2 className="w-3.5 h-3.5" /> Remove All</>
                    )}
                  </button>
                ) : (
                  // PARTIAL / NONE added → show themed Add button
                  <button
                    onClick={(e) => { e.stopPropagation(); handleBulkAdd(list.id, list.stocks); }}
                    disabled={isLoading || multiAdding}
                    className={`flex items-center justify-center gap-2 w-full py-2 rounded-lg text-xs font-semibold text-white transition-all disabled:opacity-50 disabled:cursor-not-allowed ${c.btn}`}
                  >
                    {isLoading ? (
                      <><RefreshCw className="w-3.5 h-3.5 animate-spin" /> Adding…</>
                    ) : (
                      <>
                        <Plus className="w-3.5 h-3.5" />
                        {alreadyAdded > 0 ? `Add Remaining ${list.stocks.length - alreadyAdded}` : `Add All ${list.stocks.length}`}
                      </>
                    )}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Risk Configuration */}
        <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-6">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-lg font-semibold">Risk Parameters</h2>
            {saved && <span className="text-emerald-400 text-sm">✓ Saved</span>}
          </div>

          {config ? (
            <div className="space-y-4">
              {[
                {
                  label: "Risk per Trade (%)",
                  field: "risk_per_trade_pct" as keyof RiskConfig,
                  step: "0.001",
                },
                {
                  label: "Max Daily Loss (₹)",
                  field: "max_daily_loss_inr" as keyof RiskConfig,
                  step: "100",
                },
                {
                  label: "Max Daily Loss (%)",
                  field: "max_daily_loss_pct" as keyof RiskConfig,
                  step: "0.01",
                },
                {
                  label: "Max Account Drawdown (%)",
                  field: "max_account_drawdown_pct" as keyof RiskConfig,
                  step: "0.01",
                },
                {
                  label: "Cooldown (minutes)",
                  field: "cooldown_minutes" as keyof RiskConfig,
                  step: "1",
                },
                {
                  label: "Min ATR (%)",
                  field: "min_atr_pct" as keyof RiskConfig,
                  step: "0.001",
                },
                {
                  label: "Max ATR (%)",
                  field: "max_atr_pct" as keyof RiskConfig,
                  step: "0.001",
                },
                {
                  label: "Max Position Size (%)",
                  field: "max_position_pct" as keyof RiskConfig,
                  step: "0.01",
                },
                {
                  label: "Max Concurrent Positions",
                  field: "max_concurrent_positions" as keyof RiskConfig,
                  step: "1",
                },
              ].map(({ label, field, step }) => (
                <div key={field}>
                  <label className="text-xs text-gray-500 uppercase tracking-wide block mb-1">
                    {label}
                  </label>
                  <input
                    type="number"
                    step={step}
                    value={config[field] as number}
                    onChange={(e) =>
                      updateField(field, parseFloat(e.target.value))
                    }
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:border-blue-500"
                  />
                </div>
              ))}

              <button
                onClick={handleSave}
                disabled={saving}
                className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white py-2.5 rounded-lg font-medium text-sm transition-colors mt-2"
              >
                {saving ? "Saving…" : "Save Risk Configuration"}
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-gray-500 text-sm">
              <RefreshCw className="w-4 h-4 animate-spin" />
              Loading configuration…
            </div>
          )}
        </div>

        {/* Watchlist + Broker */}
        <div className="space-y-5">
          {/* Watchlist */}
          <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Watchlist</h2>
              <span className="text-xs text-gray-500">
                {symbols.length} symbols
              </span>
            </div>

            {/* Add Symbol Form */}
            <form onSubmit={handleAddSymbol} className="flex gap-2 mb-4">
              <input
                type="text"
                placeholder="e.g. RELIANCE, INFY, TCS"
                value={newSymbol}
                onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
                className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 uppercase"
                maxLength={20}
              />
              <select
                value={newExchange}
                onChange={(e) => setNewExchange(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-2 text-sm focus:outline-none"
              >
                <option value="NSE">NSE</option>
                <option value="BSE">BSE</option>
              </select>
              <button
                type="submit"
                disabled={adding || !newSymbol.trim()}
                className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-3 py-2 rounded-lg text-sm font-medium transition-colors"
              >
                <Plus className="w-4 h-4" />
                {adding ? "Adding…" : "Add"}
              </button>
            </form>

            {addError && (
              <p className="text-red-400 text-xs mb-3 px-3 py-2 bg-red-950/30 border border-red-900/50 rounded-lg">
                ⚠️ {addError}
              </p>
            )}
            {addSuccess && (
              <p className="text-emerald-400 text-xs mb-3 px-3 py-2 bg-emerald-950/30 border border-emerald-900/50 rounded-lg">
                {addSuccess}
              </p>
            )}

            {symbols.length > 0 ? (
              <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
                {symbols.map((sym: any) => (
                  <div
                    key={sym.id}
                    className="flex items-center justify-between bg-gray-800/60 rounded-lg px-3 py-2.5"
                  >
                    <div>
                      <span className="font-semibold text-sm">
                        {sym.trading_symbol}
                      </span>
                      <span className="text-xs text-gray-500 ml-2">
                        {sym.exchange}
                      </span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span
                        className={`text-xs ${sym.is_active ? "text-emerald-400" : "text-gray-500"}`}
                      >
                        {sym.is_active ? "Active" : "Inactive"}
                      </span>
                      <button
                        onClick={() => handleDelete(sym.id, sym.trading_symbol)}
                        disabled={deletingId === sym.id}
                        className="text-gray-600 hover:text-red-400 transition-colors disabled:opacity-40"
                        title={`Remove ${sym.trading_symbol}`}
                      >
                        {deletingId === sym.id ? (
                          <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <Trash2 className="w-3.5 h-3.5" />
                        )}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-gray-500 text-sm text-center py-4">
                No symbols yet. Use Stock Lists above or add manually.
              </p>
            )}
          </div>

          {/* Broker Status */}
          <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-6">
            <h2 className="text-lg font-semibold mb-4">Broker Connection</h2>
            <div className="space-y-3">
              {brokerHealth &&
              brokerHealth.status !== "NOT_CONFIGURED" &&
              brokerHealth.adapter !== "none" ? (
                <>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span
                        className={`w-2 h-2 rounded-full ${
                          brokerHealth.status === "CONNECTED"
                            ? "bg-emerald-400 animate-pulse"
                            : brokerHealth.status === "CONNECTING"
                              ? "bg-yellow-400 animate-pulse"
                              : "bg-red-400"
                        }`}
                      />
                      <span className="text-sm font-medium capitalize">
                        {brokerHealth.adapter}
                      </span>
                    </div>
                    <span
                      className={`text-xs px-2 py-0.5 rounded ${
                        brokerHealth.status === "CONNECTED"
                          ? "bg-emerald-900/50 text-emerald-300"
                          : "bg-red-900/50 text-red-300"
                      }`}
                    >
                      {brokerHealth.status}
                    </span>
                  </div>
                  {brokerHealth.subscribed_symbols?.length > 0 && (
                    <p className="text-xs text-gray-500">
                      Subscribed: {brokerHealth.subscribed_symbols.join(", ")}
                    </p>
                  )}
                </>
              ) : (
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-gray-600" />
                  <span className="text-sm text-gray-400">
                    No active broker configured
                  </span>
                </div>
              )}
              <p className="text-xs text-gray-600 mt-2">
                Configure broker credentials (Upstox, Shoonya) in the{" "}
                <code className="text-gray-400">.env</code> file.
              </p>
            </div>
          </div>

          {/* ── Upstox Token (FIX #10) ── */}
          <div className="bg-gray-900/80 border border-amber-800/40 rounded-xl p-6">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-amber-400 text-base">🔑</span>
              <h2 className="text-lg font-semibold">Upstox Access Token</h2>
            </div>
            <p className="text-xs text-gray-500 mb-4">
              Upstox tokens expire daily. Paste a fresh token here — no Docker
              restart required. The broker recovery task will reconnect within
              60 s.
            </p>
            <textarea
              rows={3}
              placeholder="Paste your Upstox access token here…"
              value={upstoxToken}
              onChange={(e) => setUpstoxToken(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:border-amber-500 resize-none mb-3"
            />
            {tokenMsg && (
              <p className="text-emerald-400 text-xs mb-3 px-3 py-2 bg-emerald-950/30 border border-emerald-900/50 rounded-lg">
                {tokenMsg}
              </p>
            )}
            {tokenError && (
              <p className="text-red-400 text-xs mb-3 px-3 py-2 bg-red-950/30 border border-red-900/50 rounded-lg">
                ⚠️ {tokenError}
              </p>
            )}
            <button
              onClick={handleSaveToken}
              disabled={tokenSaving || !upstoxToken.trim()}
              className="w-full bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white py-2 rounded-lg font-medium text-sm transition-colors"
            >
              {tokenSaving ? "Saving…" : "Update Token"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
