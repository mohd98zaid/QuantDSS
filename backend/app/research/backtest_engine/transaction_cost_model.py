"""
Transaction Cost Model — NSE intraday cost model for backtesting.

Extracted from the existing BacktestEngine for reuse across modules.
Models: brokerage, STT, exchange charges, SEBI fees, GST, stamp duty.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TransactionCosts:
    """Breakdown of transaction costs."""
    brokerage: float = 0.0
    stt: float = 0.0
    exchange_charge: float = 0.0
    sebi_fee: float = 0.0
    gst: float = 0.0
    stamp_duty: float = 0.0
    total: float = 0.0


class TransactionCostModel:
    """NSE intraday transaction cost calculator."""

    @staticmethod
    def compute(
        entry_price: float,
        exit_price: float,
        quantity: int,
        signal_type: str = "BUY",
    ) -> TransactionCosts:
        entry_v = entry_price * quantity
        exit_v = exit_price * quantity

        if signal_type == "BUY":
            buy_v, sell_v = entry_v, exit_v
        else:
            buy_v, sell_v = exit_v, entry_v

        both_v = entry_v + exit_v

        brokerage = min(both_v * 0.0003, 40.0)
        stt = sell_v * 0.00025
        exchange_charge = both_v * 0.0000297
        sebi_fee = both_v * 0.000001
        gst = (brokerage + exchange_charge + sebi_fee) * 0.18
        stamp_duty = buy_v * 0.00003

        total = brokerage + stt + exchange_charge + sebi_fee + gst + stamp_duty

        return TransactionCosts(
            brokerage=round(brokerage, 4),
            stt=round(stt, 4),
            exchange_charge=round(exchange_charge, 4),
            sebi_fee=round(sebi_fee, 4),
            gst=round(gst, 4),
            stamp_duty=round(stamp_duty, 4),
            total=round(total, 4),
        )
