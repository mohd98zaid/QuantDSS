"""
YFinanceAdapter — Historical data wrapper using yfinance.
No authentication required. Free and unlimited.
"""

import pandas as pd

from app.core.logging import logger
from app.ingestion.broker_adapter import BrokerAdapter


class YFinanceAdapter(BrokerAdapter):
    """yfinance adapter for historical OHLCV data — no auth required."""

    def __init__(self):
        super().__init__(name="yfinance")

    async def connect(self) -> bool:
        """yfinance doesn't need a persistent connection."""
        self.is_connected = True
        return True

    async def disconnect(self) -> None:
        self.is_connected = False

    async def subscribe(self, symbols: list[str]) -> None:
        """Not applicable for historical data."""
        pass

    async def unsubscribe(self, symbols: list[str]) -> None:
        """Not applicable for historical data."""
        pass

    async def get_connection_status(self) -> dict:
        return {
            "adapter": self.name,
            "status": "AVAILABLE",
            "subscribed_symbols": [],
            "last_tick_at": None,
        }

    @staticmethod
    def download_history(
        symbol: str,
        period: str = "5y",
        interval: str = "1d",
        exchange_suffix: str = ".NS",
    ) -> pd.DataFrame | None:
        """
        Download historical OHLCV data for an NSE symbol.

        Args:
            symbol: NSE symbol code (e.g., "RELIANCE")
            period: Data period (e.g., "5y", "1y", "6mo")
            interval: Candle interval (e.g., "1d", "1h")
            exchange_suffix: NSE suffix for Yahoo Finance

        Returns:
            DataFrame with OHLCV data, or None if download fails.
        """
        import yfinance as yf

        ticker = f"{symbol}{exchange_suffix}"
        logger.info(f"Downloading {period} of {interval} data for {ticker}")

        try:
            data = yf.download(ticker, period=period, interval=interval, progress=False)
            if data.empty:
                logger.warning(f"No data returned for {ticker}")
                return None

            # Flatten multi-level columns if present
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)

            logger.info(f"Downloaded {len(data)} candles for {ticker}")
            return data
        except Exception as e:
            logger.error(f"Failed to download data for {ticker}: {e}")
            return None
