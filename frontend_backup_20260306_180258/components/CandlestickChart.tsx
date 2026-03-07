"use client";

import {
  ComposedChart,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  Bar,
  ErrorBar,
} from "recharts";

export interface OHLCCandle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface ChartCandle {
  time: string;
  label: string;
  // For the body bar: [low of body, high of body]
  body: [number, number];
  // For wick: shown via errorBar
  wickHigh: number;
  wickLow: number;
  close: number;
  open: number;
  high: number;
  low: number;
  volume: number;
  bullish: boolean;
}

function formatTime(isoString: string): string {
  try {
    const d = new Date(isoString);
    return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return isoString;
  }
}

function formatDate(isoString: string): string {
  try {
    const d = new Date(isoString);
    return d.toLocaleDateString("en-IN", { month: "short", day: "numeric" });
  } catch {
    return isoString;
  }
}

// Custom tooltip
function CandleTooltip({ active, payload }: any) {
  if (!active || !payload || !payload.length) return null;
  const d: ChartCandle = payload[0]?.payload;
  if (!d) return null;
  const color = d.bullish ? "#10b981" : "#ef4444";
  const change = d.close - d.open;
  const pct = ((change / d.open) * 100).toFixed(2);

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 text-xs shadow-xl min-w-[160px]">
      <p className="text-gray-400 mb-2 font-medium">{d.label}</p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        <span className="text-gray-500">Open</span>
        <span className="font-mono text-white">₹{d.open.toFixed(2)}</span>
        <span className="text-gray-500">High</span>
        <span className="font-mono text-emerald-400">₹{d.high.toFixed(2)}</span>
        <span className="text-gray-500">Low</span>
        <span className="font-mono text-red-400">₹{d.low.toFixed(2)}</span>
        <span className="text-gray-500">Close</span>
        <span className="font-mono" style={{ color }}><strong>₹{d.close.toFixed(2)}</strong></span>
        <span className="text-gray-500">Change</span>
        <span style={{ color }}>
          {change >= 0 ? "+" : ""}
          {change.toFixed(2)} ({pct}%)
        </span>
        <span className="text-gray-500">Volume</span>
        <span className="font-mono text-gray-300">
          {d.volume > 1_000_000
            ? `${(d.volume / 1_000_000).toFixed(2)}M`
            : d.volume > 1_000
            ? `${(d.volume / 1_000).toFixed(1)}K`
            : d.volume.toString()}
        </span>
      </div>
    </div>
  );
}

interface Props {
  candles: OHLCCandle[];
  timeframe: string;
}

export function CandlestickChart({ candles, timeframe }: Props) {
  if (!candles || candles.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-72 text-gray-500 gap-3">
        <span className="text-5xl">📉</span>
        <p className="text-sm">No candle data available</p>
        <p className="text-xs text-gray-600">
          Ensure the broker is connected and symbols are subscribed
        </p>
      </div>
    );
  }

  const isIntraday = ["1min", "5min", "15min", "30min"].includes(timeframe);

  // Transform candles into chart data
  const data: ChartCandle[] = candles.map((c) => {
    const bullish = c.close >= c.open;
    const bodyLow = Math.min(c.open, c.close);
    const bodyHigh = Math.max(c.open, c.close);
    return {
      time: c.time,
      label: isIntraday ? formatTime(c.time) : formatDate(c.time),
      body: [bodyLow, bodyHigh],
      wickHigh: c.high - bodyHigh,
      wickLow: bodyLow - c.low,
      close: c.close,
      open: c.open,
      high: c.high,
      low: c.low,
      volume: c.volume,
      bullish,
    };
  });

  // Price domain with 0.5% padding
  const allPrices = candles.flatMap((c) => [c.high, c.low]);
  const priceMin = Math.min(...allPrices);
  const priceMax = Math.max(...allPrices);
  const padding = (priceMax - priceMin) * 0.05;
  const domainMin = Math.floor((priceMin - padding) * 10) / 10;
  const domainMax = Math.ceil((priceMax + padding) * 10) / 10;

  // Determine bar size vs number of candles
  const barSize = Math.max(2, Math.min(14, Math.floor(800 / data.length) - 2));

  return (
    <ResponsiveContainer width="100%" height={340}>
      <ComposedChart
        data={data}
        margin={{ top: 10, right: 20, left: 10, bottom: 0 }}
        barCategoryGap="20%"
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
        <XAxis
          dataKey="label"
          stroke="#4b5563"
          tick={{ fill: "#6b7280", fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          domain={[domainMin, domainMax]}
          stroke="#4b5563"
          tick={{ fill: "#6b7280", fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => `₹${v.toLocaleString("en-IN")}`}
          width={75}
        />
        <Tooltip content={<CandleTooltip />} cursor={{ fill: "rgba(255,255,255,0.03)" }} />

        {/* Candle body */}
        <Bar dataKey="body" barSize={barSize} isAnimationActive={false}>
          {data.map((entry, index) => (
            <Cell
              key={`cell-${index}`}
              fill={entry.bullish ? "#10b981" : "#ef4444"}
              stroke={entry.bullish ? "#059669" : "#dc2626"}
              strokeWidth={0.5}
            />
          ))}
          {/* Wicks */}
          <ErrorBar
            dataKey="wickHigh"
            width={0}
            strokeWidth={1.5}
            stroke="#6b7280"
            direction="y"
          />
        </Bar>
      </ComposedChart>
    </ResponsiveContainer>
  );
}
