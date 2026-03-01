"use client";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";

interface EquityCurveProps {
  data: { date: string; balance: number; pnl: number }[];
  initialBalance?: number;
}

export function EquityCurveChart({
  data,
  initialBalance = 100000,
}: EquityCurveProps) {
  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-500">
        <span>No equity data available yet</span>
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={280}>
      <AreaChart
        data={data}
        margin={{ top: 5, right: 20, left: 10, bottom: 5 }}
      >
        <defs>
          <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="date" stroke="#6b7280" fontSize={11} tickLine={false} />
        <YAxis
          stroke="#6b7280"
          fontSize={11}
          tickLine={false}
          tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "#111827",
            border: "1px solid #374151",
            borderRadius: "8px",
          }}
          labelStyle={{ color: "#9ca3af" }}
          formatter={(value: number) => [
            `₹${value.toLocaleString("en-IN")}`,
            "Balance",
          ]}
        />
        <ReferenceLine
          y={initialBalance}
          stroke="#4b5563"
          strokeDasharray="3 3"
        />
        <Area
          type="monotone"
          dataKey="balance"
          stroke="#3b82f6"
          fill="url(#equityGrad)"
          strokeWidth={2}
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

interface DrawdownProps {
  data: { date: string; drawdown_pct: number }[];
}

export function DrawdownChart({ data }: DrawdownProps) {
  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-500">
        <span>No drawdown data available yet</span>
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={280}>
      <AreaChart
        data={data}
        margin={{ top: 5, right: 20, left: 10, bottom: 5 }}
      >
        <defs>
          <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#ef4444" stopOpacity={0.4} />
            <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="date" stroke="#6b7280" fontSize={11} tickLine={false} />
        <YAxis
          stroke="#6b7280"
          fontSize={11}
          tickLine={false}
          tickFormatter={(v) => `${v.toFixed(1)}%`}
          reversed
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "#111827",
            border: "1px solid #374151",
            borderRadius: "8px",
          }}
          labelStyle={{ color: "#9ca3af" }}
          formatter={(value: number) => [`${value.toFixed(2)}%`, "Drawdown"]}
        />
        <Area
          type="monotone"
          dataKey="drawdown_pct"
          stroke="#ef4444"
          fill="url(#ddGrad)"
          strokeWidth={2}
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

interface PnlBarProps {
  data: { date: string; pnl: number }[];
}

export function DailyPnlChart({ data }: PnlBarProps) {
  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-500">
        <span>No P&amp;L data available yet</span>
      </div>
    );
  }

  // Color individual bars based on P&L
  const coloredData = data.map((d) => ({
    ...d,
    fill: d.pnl >= 0 ? "#10b981" : "#ef4444",
  }));

  return (
    <ResponsiveContainer width="100%" height={280}>
      <AreaChart
        data={coloredData}
        margin={{ top: 5, right: 20, left: 10, bottom: 5 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="date" stroke="#6b7280" fontSize={11} tickLine={false} />
        <YAxis
          stroke="#6b7280"
          fontSize={11}
          tickLine={false}
          tickFormatter={(v) => `₹${v.toLocaleString("en-IN")}`}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "#111827",
            border: "1px solid #374151",
            borderRadius: "8px",
          }}
          labelStyle={{ color: "#9ca3af" }}
          formatter={(value: number) => [
            `₹${value.toLocaleString("en-IN")}`,
            "P&L",
          ]}
        />
        <ReferenceLine y={0} stroke="#4b5563" />
        <Area
          type="monotone"
          dataKey="pnl"
          stroke="#6366f1"
          fill="#6366f133"
          strokeWidth={2}
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
