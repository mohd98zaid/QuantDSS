"""
Market Replay Engine — Simulates historical trading sessions.

Loads historical tick data from CSV, controls replay speed, and injects ticks 
directly into the live QuantDSS pipeline via CandleAggregator.
"""
import asyncio
import csv
import io
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
import uuid

from app.core.logging import logger
from app.ingestion.candle_aggregator import CandleAggregator
try:
    from app.ingestion.candle_aggregator import candle_aggregator
except ImportError:
    candle_aggregator = CandleAggregator()

import pytz
IST = pytz.timezone("Asia/Kolkata")


class MarketReplayEngine:
    """Historical tick simulation engine."""

    # Configurable speeds
    SPEED_REALTIME = 1
    SPEED_FAST = 5
    SPEED_FASTER = 10
    SPEED_TURBO = 50
    SPEED_MAX = 100

    def __init__(self):
        self.is_running = False
        self.is_paused = False
        self.current_session_id: Optional[str] = None
        self.replay_speed = self.SPEED_REALTIME
        self.replay_task: Optional[asyncio.Task] = None
        
        # Metrics tracking
        self.metrics = {
            "ticks_processed": 0,
            "candles_generated": 0,
            "signals_generated": 0, # Note: this will require pipeline callbacks or DB querying at the end
            "paper_trades": 0,
            "start_time": None,
            "end_time": None,
            "symbols": [],
        }
        
    def start_replay(self, csv_data: str, speed: int = SPEED_REALTIME) -> str:
        """Starts a new replay session from CSV string data."""
        if self.is_running:
            raise RuntimeError("A replay session is already running.")
            
        self.is_running = True
        self.is_paused = False
        self.replay_speed = speed
        self.current_session_id = f"replay_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        
        self.metrics = {
            "ticks_processed": 0,
            "candles_generated": 0,
            "signals_generated": 0,
            "paper_trades": 0,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": None,
            "symbols": [],
        }

        # Parse CSV synchronously once
        ticks = self._parse_csv(csv_data)
        
        # Reset the CandleAggregator session to clear any previous data
        candle_aggregator.reset_session()
        
        # Start async replay loop
        self.replay_task = asyncio.create_task(self._replay_loop(ticks))
        
        logger.info(f"MarketReplayEngine: Started session {self.current_session_id} at {speed}x speed with {len(ticks)} ticks")
        return self.current_session_id

    def pause_replay(self) -> None:
        """Pauses the current replay session."""
        if not self.is_running:
            raise RuntimeError("No replay session is running.")
        self.is_paused = True
        logger.info(f"MarketReplayEngine: Session {self.current_session_id} paused")

    def resume_replay(self) -> None:
        """Resumes a paused replay session."""
        if not self.is_running:
            raise RuntimeError("No replay session is running.")
        self.is_paused = False
        logger.info(f"MarketReplayEngine: Session {self.current_session_id} resumed")

    def stop_replay(self) -> dict:
        """Stops the current replay session and returns final metrics."""
        if not self.is_running:
            raise RuntimeError("No replay session is running.")
            
        self.is_running = False
        if self.replay_task and not self.replay_task.done():
            self.replay_task.cancel()
            
        self.metrics["end_time"] = datetime.now(timezone.utc).isoformat()
        logger.info(f"MarketReplayEngine: Session {self.current_session_id} stopped. Processed {self.metrics['ticks_processed']} ticks.")
        return self.get_status()

    def get_status(self) -> dict:
        """Returns the current status of the replay session."""
        return {
            "session_id": self.current_session_id,
            "is_running": self.is_running,
            "is_paused": self.is_paused,
            "speed": self.replay_speed,
            "metrics": self.metrics,
        }

    def _parse_csv(self, csv_string: str) -> List[dict]:
        """
        Parses tick CSV data and sorts by timestamp.
        Format expected: timestamp,symbol,price,volume
        """
        ticks = []
        reader = csv.DictReader(io.StringIO(csv_string))
        symbols_seen = set()
        
        # Validate columns
        required_cols = {"timestamp", "symbol", "price", "volume"}
        if not required_cols.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"CSV must contain columns: {required_cols}")

        for row in reader:
            try:
                # Expecting format 'YYYY-MM-DD HH:MM:SS'
                ts_naive = datetime.strptime(row["timestamp"].strip(), "%Y-%m-%d %H:%M:%S")
                # Attach IST timezone manually
                ts_ist = IST.localize(ts_naive)
                
                # Check session bounds: only process 09:15 to 15:30
                h, m = ts_ist.hour, ts_ist.minute
                current_min = h * 60 + m
                open_min = 9 * 60 + 15
                close_min = 15 * 60 + 30
                
                if not (open_min <= current_min <= close_min):
                    continue  # Ignore ticks outside market hours

                symbol = row["symbol"].strip()
                symbols_seen.add(symbol)
                
                ticks.append({
                    "timestamp": ts_ist,
                    "symbol": symbol,
                    "ltp": float(row["price"]),
                    "volume": int(float(row["volume"])),
                    "instrument_key": symbol  # Fallback instrument key
                })
            except Exception as e:
                logger.warning(f"MarketReplayEngine: Failed to parse tick row {row}: {e}")
                
        self.metrics["symbols"] = list(symbols_seen)
        
        # Ensure sequential order
        ticks.sort(key=lambda t: t["timestamp"])
        return ticks

    async def _replay_loop(self, ticks: List[dict]):
        """Asynchronous loop that simulates real-time tick injection."""
        try:
            if not ticks:
                logger.warning("MarketReplayEngine: No valid ticks to replay.")
                self.stop_replay()
                return

            last_tick_time = ticks[0]["timestamp"]
            
            for tick in ticks:
                if not self.is_running:
                    break
                    
                while self.is_paused:
                    await asyncio.sleep(0.5)

                # Simulate real-time delay relative to previous tick
                current_tick_time = tick["timestamp"]
                time_diff = (current_tick_time - last_tick_time).total_seconds()
                
                if time_diff > 0:
                    delay = time_diff / self.replay_speed
                    # Cap large delays for massive gaps (e.g. feed down) to max 5 actual seconds
                    delay = min(delay, 5.0) 
                    await asyncio.sleep(delay)
                
                last_tick_time = current_tick_time

                # Inject tick into CandleAggregator
                # Stamp as replay so downstream workers can bypass market hours checks
                tick["is_replay"] = True
                tick["replay_session_id"] = self.current_session_id
                completed_candle = await candle_aggregator.process_tick(tick)
                
                self.metrics["ticks_processed"] += 1
                if completed_candle:
                    self.metrics["candles_generated"] += 1
            
            # Replay finished
            if self.is_running:
                logger.info("MarketReplayEngine: Finished processing all ticks.")
                self.stop_replay()
                
        except asyncio.CancelledError:
            logger.info("MarketReplayEngine: Replay loop cancelled.")
        except Exception as e:
            logger.exception(f"MarketReplayEngine: Error in replay loop: {e}")
            self.stop_replay()

# Global Singleton
market_replay_engine = MarketReplayEngine()
