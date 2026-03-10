/**
 * QuantDSS API Client — Fetch wrapper with JWT auth.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

let accessToken: string | null = null;

export function setToken(token: string) {
  accessToken = token;
  if (typeof window !== "undefined") {
    localStorage.setItem("quantdss_token", token);
  }
}

export function getToken(): string | null {
  if (accessToken) return accessToken;
  if (typeof window !== "undefined") {
    accessToken = localStorage.getItem("quantdss_token");
  }
  return accessToken;
}

export function clearToken() {
  accessToken = null;
  if (typeof window !== "undefined") {
    localStorage.removeItem("quantdss_token");
  }
}

async function apiFetch<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) || {}),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}/v1${endpoint}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    clearToken();
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new Error("Unauthorized");
  }

  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "Unknown error" }));
    throw new Error(error.detail || `API Error: ${response.status}`);
  }

  if (response.status === 204 || response.headers.get("content-length") === "0") {
    return undefined as T;
  }

  const text = await response.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

// Auth
export const login = (username: string, password: string) =>
  apiFetch<{ access_token: string; token_type: string; expires_in: number }>(
    "/auth/login",
    { method: "POST", body: JSON.stringify({ username, password }) },
  );

// Health
export const getHealth = () =>
  apiFetch<{ status: string; database: string; redis: string }>("/health");
export const getBrokerHealth = () =>
  apiFetch<{ adapter: string; status: string; subscribed_symbols?: string[]; last_tick_at?: string | null }>("/health/broker");
export const getMarketStatus = () =>
  apiFetch<{ is_open: boolean; status: string; current_time_ist: string }>(
    "/health/market",
  );

// Symbols
export const getSymbols = () => apiFetch<any[]>("/symbols");
export const addSymbol = (trading_symbol: string, exchange: string = "NSE") =>
  apiFetch<any>("/symbols", {
    method: "POST",
    body: JSON.stringify({ trading_symbol, exchange }),
  });
export const deleteSymbol = (id: number) =>
  apiFetch<any>(`/symbols/${id}`, { method: "DELETE" });

// Strategies
export const getStrategies = () => apiFetch<any[]>("/strategies");

// Signals
export const getSignals = (params?: Record<string, string>) => {
  const query = params ? "?" + new URLSearchParams(params).toString() : "";
  return apiFetch<any>(`/signals${query}`);
};

// Risk
export const getRiskConfig = () => apiFetch<any>("/risk/config");
export const getRiskState = () => apiFetch<any>("/risk/state");
export const updateRiskConfig = (data: any) =>
  apiFetch<any>("/risk/config", { method: "PUT", body: JSON.stringify(data) });

// Candles
export const getCandles = (
  symbol: string,
  timeframe: string = "5min",
  limit: number = 100,
) => apiFetch<any>(`/candles/${symbol}/${timeframe}?limit=${limit}`);

// Seed historical data from Upstox (primary) or Yahoo Finance (fallback)
export const seedCandles = (symbol: string, timeframe: string = "5min") =>
  apiFetch<{
    symbol: string;
    timeframe: string;
    candles_seeded: number;
    source: string;
    from: string | null;
    to: string | null;
    instrument_key: string;
  }>(`/market-data/seed/${symbol}?timeframe=${timeframe}`, { method: "POST" });

// Seed today's intraday candles from Upstox (requires valid token)
export const seedIntraday = (symbol: string, timeframe: string = "5min") =>
  apiFetch<{
    symbol: string;
    timeframe: string;
    candles_seeded: number;
    source: string;
    instrument_key: string;
  }>(`/market-data/intraday/${symbol}?timeframe=${timeframe}`, {
    method: "POST",
  });

// Trades / Journal
export const getTrades = (params?: Record<string, string>) => {
  const query = params ? "?" + new URLSearchParams(params).toString() : "";
  return apiFetch<any>(`/trades${query}`);
};

// On-demand Signal Scanner
export const getScannerStrategies = () => apiFetch<any[]>("/scanner/strategies");

export const scanSymbol = (data: {
  symbol: string;
  strategy: string;
  timeframe: string;
  candles_limit?: number;
}) => apiFetch<any>("/scanner/analyze", { method: "POST", body: JSON.stringify(data) });

export const executePaperTradeApi = (data: {
  symbol: string;
  instrument_key: string;
  direction: string;
  quantity: number;
  entry_price: number;
  stop_loss: number;
  target_price: number;
}) => apiFetch<any>("/paper/execute", { method: "POST", body: JSON.stringify(data) });

// Paper Trading — balance, positions, history, close, reset, export
export const getPaperBalance = () =>
  apiFetch<{ paper_balance: number }>("/paper/balance");
export const getPaperPositions = () =>
  apiFetch<any[]>("/paper/positions");
export const getPaperHistory = () =>
  apiFetch<any[]>("/paper/history");
export const closePaperTrade = (tradeId: number, exitPrice: number) =>
  apiFetch<any>(`/paper/close/${tradeId}?exit_price=${exitPrice}`, { method: "POST" });
export const resetPaperData = () =>
  apiFetch<any>("/paper/data", { method: "DELETE" });
export const exportPaperTrades = () =>
  fetch(`${API_BASE}/v1/paper/export`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });

// Auto Trader — trading mode, halt, resume, close all
export const getTradingMode = () =>
  apiFetch<any>("/auto-trader/trading-mode");
export const setTradingMode = (mode: string, confirm_live: boolean = false) =>
  apiFetch<any>("/auto-trader/trading-mode", {
    method: "POST",
    body: JSON.stringify({ mode, confirm_live }),
  });
export const emergencyHalt = () =>
  apiFetch<any>("/auto-trader/halt", { method: "POST" });
export const resumeTrading = () =>
  apiFetch<any>("/auto-trader/resume", { method: "POST" });
export const killSwitchCloseAll = () =>
  apiFetch<any>("/auto-trader/close-all", { method: "POST" });

// Performance Analytics
export const getEquityCurve = (days: number = 30) =>
  apiFetch<any>(`/performance/equity-curve?days=${days}`);
export const getDrawdown = (days: number = 30) =>
  apiFetch<any>(`/performance/drawdown?days=${days}`);
export const getPerformanceByStrategy = () =>
  apiFetch<any[]>("/performance/by-strategy");
