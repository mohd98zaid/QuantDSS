"""
Strategy Performance Monitor

Tracks rolling performance metrics per strategy over the last 100 trades.
Auto-disables failing strategies if thresholds are breached, and notifies Telegram/Dashboard.
"""
import asyncio
import numpy as np
from datetime import datetime, timezone
from sqlalchemy import text
import httpx

from app.core.database import async_session_factory
from app.core.logging import logger
from app.core.config import settings
from app.core.redis import publish

class StrategyHealthMonitor:
    NAME = "strategy-health-monitor"
    EVALUATION_WINDOW = 100
    SHARPE_THRESHOLD = 0.2
    DRAWDOWN_THRESHOLD = 15.0 # Max DD% allowed before disable

    async def _send_telegram_alert(self, message: str):
        bot_token = getattr(settings, "telegram_bot_token", None)
        chat_id = getattr(settings, "telegram_chat_id", None)
        if not bot_token or not chat_id:
            return
            
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient() as client:
                await client.post(url, json={"chat_id": chat_id, "text": message})
        except Exception as e:
            logger.error(f"[{self.NAME}] Failed to send Telegram alert: {e}")

    async def _disable_strategy(self, strategy_name: str, sharpe: float, drawdown: float, db):
        logger.warning(
            f"[{self.NAME}] Disabling strategy {strategy_name}: Sharpe={sharpe:.2f}, DD={drawdown:.2f}%"
        )
        # Assuming strategy table has a 'is_active' boolean flag
        try:
            await db.execute(
                text("UPDATE strategy SET is_active = FALSE WHERE name = :name"),
                {"name": strategy_name}
            )
            await db.commit()
            
            alert_msg = f"🚨 *QuantDSS Strategy Disabled*\n{strategy_name} breached health thresholds.\nSharpe: {sharpe:.2f}\nDrawdown: {drawdown:.2f}%"
            await self._send_telegram_alert(alert_msg)
            
            # Send SSE alert via Redis (Layer 7 and Integration requirement)
            alert_event = {
                "event": "system_alert",
                "message": f"Strategy {strategy_name} auto-disabled due to performance degradation.",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await publish("ui:events", alert_event)
            
        except Exception as e:
            logger.error(f"[{self.NAME}] Failed to disable strategy {strategy_name}: {e}")

    async def evaluate_strategies(self):
        async with async_session_factory() as db:
            try:
                # Fetch distinct strategy names we need to evaluate
                strat_query = await db.execute(text("SELECT name FROM strategy WHERE is_active = TRUE"))
                active_strategies = [row[0] for row in strat_query.fetchall()]

                for strategy in active_strategies:
                    # Fetch the last 100 closed trades for this strategy 
                    query = text("""
                        SELECT l.realised_pnl, l.risk_amount 
                        FROM live_trade l
                        JOIN signal s ON l.signal_id = s.id
                        WHERE l.status = 'CLOSED' AND s.strategy_name = :strategy
                        ORDER BY l.exit_time DESC
                        LIMIT :limit
                    """)
                    result = await db.execute(query, {"strategy": strategy, "limit": self.EVALUATION_WINDOW})
                    trades = result.fetchall()

                    if len(trades) < 20:
                        # Not enough data to confidently assert Sharpe and DD
                        continue
                    
                    pnls = []
                    r_multiples = []
                    wins = 0

                    for trade in trades:
                        pnl = float(trade[0] or 0)
                        risk = float(trade[1] or 1)
                        pnls.append(pnl)
                        r_multiples.append(pnl / risk if risk > 0 else 0)
                        if pnl > 0:
                            wins += 1

                    pnls = pnls[::-1] # chronologically ordered mapping
                    wins = wins
                    win_rate = wins / len(trades)
                    avg_r = np.mean(r_multiples)

                    # Sharpe logic (R-based simplified)
                    std_dev = np.std(r_multiples)
                    sharpe = (avg_r / std_dev) if std_dev > 0 else 0.0

                    # Max Drawdown logic
                    cumulative_pnl = np.cumsum(pnls)
                    peaks = np.maximum.accumulate(cumulative_pnl)
                    drawdowns = (peaks - cumulative_pnl) # Note: user requested percentage. Since we don't have account balance over time, we use flat DD or estimated %. Let's use simple estimate assuming 100k balance.
                    max_dd_inr = np.max(drawdowns) if len(drawdowns) > 0 else 0
                    max_dd_pct = (max_dd_inr / 100000.0) * 100 # Rough proxy for percentages

                    # Send to dashboard stream
                    health_event = {
                        "event": "strategy_health_update",
                        "strategy": strategy,
                        "rolling_sharpe": float(sharpe),
                        "rolling_win_rate": float(win_rate),
                        "avg_R_multiple": float(avg_r),
                        "max_drawdown": float(max_dd_pct)
                    }
                    await publish("ui:events", health_event)

                    if sharpe < self.SHARPE_THRESHOLD or max_dd_pct > self.DRAWDOWN_THRESHOLD:
                        await self._disable_strategy(strategy, sharpe, max_dd_pct, db)

            except Exception as e:
                logger.error(f"[{self.NAME}] Failed health monitor cycle: {e}")

    async def run(self):
        logger.info(f"[{self.NAME}] Initialized. Monitoring past 100 trades for performance drift.")
        while True:
            await self.evaluate_strategies()
            await asyncio.sleep(300) # Re-evaluate every 5 minutes

if __name__ == "__main__":
    import uvloop
    uvloop.install()
    asyncio.run(StrategyHealthMonitor().run())
