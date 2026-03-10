import asyncio
from app.api.routers.scanner import _auto_trade_hook
from app.core.database import Base, engine

class MockResult:
    def __init__(self, symbol, signal, entry, sl, tp, rr):
        self.symbol = symbol
        self.signal = signal
        self.entry_price = entry
        self.stop_loss = sl
        self.target_price = tp
        self.risk_reward = rr
        self.rsi = 50.0
        self.trend = "BULLISH"
        self.change_pct = 1.5

async def run():
    results = [
        MockResult("RELIANCE", "BUY", 3000, 2980, 3040, 2.0),
        MockResult("TCS", "SELL", 4000, 4020, 3960, 2.0)
    ]
    print("Triggering auto_trade_hook with mock results...")
    await _auto_trade_hook(results, "ema_crossover", "1m")
    print("Done!")

if __name__ == "__main__":
    asyncio.run(run())
