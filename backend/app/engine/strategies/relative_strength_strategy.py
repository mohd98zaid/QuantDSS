"""
RelativeStrengthStrategy — Trade stocks outperforming/underperforming NIFTY.

Architecture doc: Section 6.4 — Relative Strength.

Concept:
  A stock that holds up while the index falls (or rallies while the
  index is flat) shows institutional accumulation. We enter in the
  direction of the outperformance once price confirms via VWAP.

Entry Conditions (LONG):
  1. stock_return_1h > NIFTY_return_1h + 1.5% (relative outperformance)
  2. price > VWAP (trend confirmation)
  3. RelVol ≥ 1.2× (real buying behind the move)

Entry Conditions (SHORT):
  1. stock_return_1h < NIFTY_return_1h - 1.5% (relative underperformance)
  2. price < VWAP
  3. RelVol ≥ 1.2×

Stop Loss:
  LONG: entry - 1.5×ATR
  SHORT: entry + 1.5×ATR

Target:
  2×ATR above/below entry

NIFTY return injection:
  `index_return_1h` is passed into this strategy via the `params` dict
  at pipeline time. The pipeline fetches it from MarketDataCache and
  sets `params["nifty_return_1h"]` before calling `evaluate()`.
  If absent, the strategy returns None (no trade without index context).

Best conditions:
  Sector rotation days, earnings-adjacent sessions for non-reporting stocks.
"""

import pandas as pd

from app.core.logging import logger
from app.engine.base_strategy import BaseStrategy, RawSignal
from app.engine.indicators import IndicatorEngine


class RelativeStrengthStrategy(BaseStrategy):
    """
    Relative Strength — Long stocks outperforming NIFTY, short underperformers.
    Requires `nifty_return_1h` to be injected into params at runtime.
    """

    @property
    def strategy_type(self) -> str:
        return "relative_strength"

    @property
    def min_candles_required(self) -> int:
        # Need at least 60 bars (1h) for the return calculation
        return max(
            60,
            self.params.get("atr_period", 14),
            self.params.get("volume_ma_period", 20),
        ) + 5

    def evaluate(self, candles: pd.DataFrame, symbol_id: int) -> RawSignal | None:
        """Evaluate relative strength conditions."""
        if len(candles) < self.min_candles_required:
            return None

        # NIFTY return MUST be injected by the pipeline
        nifty_return_1h: float | None = self.params.get("nifty_return_1h")
        if nifty_return_1h is None:
            return None  # No index context available — skip

        # Compute indicators if not pre-computed
        if "vwap" not in candles.columns:
            df = IndicatorEngine.compute_strategy_indicators(
                candles, self.strategy_type, self.params
            )
        else:
            df = candles

        for col in ("vwap", "atr", "volume_ma"):
            if col not in df.columns or pd.isna(df[col].iloc[-1]):
                return None

        curr = df.iloc[-1]

        close_curr  = float(curr["close"])
        vwap_curr   = float(curr["vwap"])
        atr_curr    = float(curr["atr"])
        volume_curr = float(curr["volume"])
        volume_ma   = float(curr["volume_ma"]) if not pd.isna(curr["volume_ma"]) else 0.0

        rel_vol = volume_curr / volume_ma if volume_ma > 0 else 0.0

        # 1-hour stock return: compare close now vs close 60 bars ago
        lookback = min(60, len(df) - 1)
        close_1h_ago = float(df["close"].iloc[-lookback - 1])
        if close_1h_ago <= 0:
            return None
        stock_return_1h = (close_curr - close_1h_ago) / close_1h_ago * 100  # in %

        rs_threshold  = self.params.get("rs_threshold_pct", 1.5)  # outperform by 1.5%
        rvol_thresh   = self.params.get("rel_vol_threshold", 1.2)
        atr_sl        = self.params.get("atr_multiplier_sl", 1.5)
        atr_target    = self.params.get("atr_multiplier_target", 2.0)

        candle_time = (
            df.index[-1] if isinstance(df.index, pd.DatetimeIndex)
            else df["time"].iloc[-1]
        )

        outperformance = stock_return_1h - nifty_return_1h  # positive = stock stronger

        # ── LONG: Stock outperforming NIFTY significantly ─────────────────
        if (
            outperformance >= rs_threshold   # Stock running hotter than index
            and close_curr > vwap_curr       # Price above VWAP
            and rel_vol >= rvol_thresh       # Volume confirms accumulation
        ):
            entry     = close_curr
            stop_loss = entry - atr_sl * atr_curr
            sl_dist   = entry - stop_loss
            if sl_dist <= 0:
                return None
            target = entry + atr_target * atr_curr

            score = 50.0
            score += min(20.0, outperformance * 5)     # +20 max for strong RS
            score += min(15.0, (rel_vol - 1) * 10)    # +15 max for volume
            vwap_cushion = (close_curr - vwap_curr) / atr_curr * 10
            score += min(15.0, vwap_cushion)           # +15 max for VWAP clearance
            score = min(100.0, max(0.0, score))

            logger.info(
                f"RelativeStrength BUY: symbol={symbol_id} @ {entry:.2f} "
                f"stock_ret={stock_return_1h:.2f}% NIFTY_ret={nifty_return_1h:.2f}% "
                f"RS_edge={outperformance:.2f}% RVOL={rel_vol:.2f} conf={score:.0f}"
            )
            return RawSignal(
                symbol_id=symbol_id,
                strategy_id=self.strategy_id,
                signal_type="BUY",
                entry_price=entry,
                stop_loss=stop_loss,
                target_price=target,
                atr_value=atr_curr,
                candle_time=candle_time,
                confidence_score=score,
            )

        # ── SHORT: Stock underperforming NIFTY significantly ─────────────
        if (
            outperformance <= -rs_threshold  # Stock lagging index
            and close_curr < vwap_curr       # Price below VWAP
            and rel_vol >= rvol_thresh
        ):
            entry     = close_curr
            stop_loss = entry + atr_sl * atr_curr
            sl_dist   = stop_loss - entry
            if sl_dist <= 0:
                return None
            target = entry - atr_target * atr_curr

            score = 50.0
            score += min(20.0, abs(outperformance) * 5)
            score += min(15.0, (rel_vol - 1) * 10)
            vwap_cushion = (vwap_curr - close_curr) / atr_curr * 10
            score += min(15.0, vwap_cushion)
            score = min(100.0, max(0.0, score))

            logger.info(
                f"RelativeStrength SELL: symbol={symbol_id} @ {entry:.2f} "
                f"stock_ret={stock_return_1h:.2f}% NIFTY_ret={nifty_return_1h:.2f}% "
                f"RS_edge={outperformance:.2f}% RVOL={rel_vol:.2f} conf={score:.0f}"
            )
            return RawSignal(
                symbol_id=symbol_id,
                strategy_id=self.strategy_id,
                signal_type="SELL",
                entry_price=entry,
                stop_loss=stop_loss,
                target_price=target,
                atr_value=atr_curr,
                candle_time=candle_time,
                confidence_score=score,
            )

        return None
