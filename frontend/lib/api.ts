/**
 * QuantDSS API Client — Fetch wrapper with JWT auth.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

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
    if (typeof window !== "undefined") {
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

  return response.json();
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
  apiFetch<{ adapter: string; status: string }>("/health/broker");

// Symbols
export const getSymbols = () => apiFetch<any[]>("/symbols");
export const addSymbol = (trading_symbol: string) =>
  apiFetch<any>("/symbols", {
    method: "POST",
    body: JSON.stringify({ trading_symbol }),
  });

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
  timeframe: string = "1min",
  limit: number = 100,
) => apiFetch<any>(`/candles/${symbol}/${timeframe}?limit=${limit}`);
