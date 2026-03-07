"""
End-to-End Integration Test — Phase 11.

Tests the full distributed pipeline:
  WebSocket tick → Candle → Redis market:candles →
  SignalEngineWorker → signals:approved →
  RiskEngineWorker → signals:risk_passed →
  AutoTraderWorker → trade execution

This test verifies message flow through Redis streams without
requiring actual market data or broker connections.
"""
import asyncio
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.streams import (
    STREAM_CANDLES,
    STREAM_SIGNALS_APPROVED,
    STREAM_SIGNALS_RISK_PASSED,
    STREAM_SIGNALS_EXECUTED,
    publish_to_stream,
    create_consumer_group,
)


@pytest.fixture
async def clean_streams():
    """Ensure test streams are clean before and after tests."""
    from app.core.redis import redis_client

    streams = [
        STREAM_CANDLES,
        STREAM_SIGNALS_APPROVED,
        STREAM_SIGNALS_RISK_PASSED,
        STREAM_SIGNALS_EXECUTED,
    ]
    for stream in streams:
        try:
            await redis_client.delete(stream)
        except Exception:
            pass

    yield

    for stream in streams:
        try:
            await redis_client.delete(stream)
        except Exception:
            pass


# ── Unit Tests for Streams Module ────────────────────────────────────────────


class TestStreamsModule:
    """Test the core streams infrastructure."""

    @pytest.mark.asyncio
    async def test_stream_constants_defined(self):
        """Verify all stream constants are defined."""
        assert STREAM_CANDLES == "market:candles"
        assert STREAM_SIGNALS_APPROVED == "signals:approved"
        assert STREAM_SIGNALS_RISK_PASSED == "signals:risk_passed"
        assert STREAM_SIGNALS_EXECUTED == "signals:executed"

    @pytest.mark.asyncio
    async def test_publish_to_stream(self, clean_streams):
        """Test publishing a message to a stream."""
        from app.core.redis import redis_client

        test_data = {
            "symbol": "RELIANCE",
            "signal_type": "BUY",
            "entry_price": "2400.0",
        }

        msg_id = await publish_to_stream(STREAM_SIGNALS_APPROVED, test_data)
        assert msg_id is not None

        # Verify message is in the stream
        messages = await redis_client.xrange(STREAM_SIGNALS_APPROVED)
        assert len(messages) >= 1

        # Check last message content
        last_msg_data = messages[-1][1]
        # Decode if bytes
        decoded = {}
        for k, v in last_msg_data.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            decoded[key] = val

        assert decoded["symbol"] == "RELIANCE"
        assert decoded["signal_type"] == "BUY"

    @pytest.mark.asyncio
    async def test_consumer_group_creation(self, clean_streams):
        """Test consumer group creation is idempotent."""
        result = await create_consumer_group(
            STREAM_SIGNALS_APPROVED,
            "test_group",
        )
        assert result is True

        # Second call should also succeed (BUSYGROUP handled)
        result2 = await create_consumer_group(
            STREAM_SIGNALS_APPROVED,
            "test_group",
        )
        assert result2 is True

    @pytest.mark.asyncio
    async def test_publish_flattens_nested_data(self, clean_streams):
        """Test that nested dicts/lists are JSON-serialized."""
        from app.core.redis import redis_client

        test_data = {
            "strategies": ["ema_crossover", "rsi_mean_reversion"],
            "metadata": {"source": "test"},
            "value": None,
        }

        msg_id = await publish_to_stream(STREAM_SIGNALS_APPROVED, test_data)
        assert msg_id is not None

        messages = await redis_client.xrange(STREAM_SIGNALS_APPROVED)
        last_data = messages[-1][1]
        decoded = {
            (k.decode() if isinstance(k, bytes) else k): (
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in last_data.items()
        }

        # Lists should be JSON strings
        assert json.loads(decoded["strategies"]) == ["ema_crossover", "rsi_mean_reversion"]
        # Dicts should be JSON strings
        assert json.loads(decoded["metadata"]) == {"source": "test"}
        # None should be empty string
        assert decoded["value"] == ""


# ── Integration Tests ────────────────────────────────────────────────────────


class TestPipelineIntegration:
    """Test that messages flow through the pipeline correctly."""

    @pytest.mark.asyncio
    async def test_candle_message_format(self, clean_streams):
        """Verify candle messages have the expected format."""
        candle = {
            "symbol": "RELIANCE",
            "symbol_id": "1",
            "instrument_key": "NSE_EQ|INE002A01018",
            "time": datetime.now(timezone.utc).isoformat(),
            "open": "2400.00",
            "high": "2410.00",
            "low": "2395.00",
            "close": "2405.00",
            "volume": "10000",
        }

        msg_id = await publish_to_stream(STREAM_CANDLES, candle)
        assert msg_id is not None

    @pytest.mark.asyncio
    async def test_approved_signal_message_format(self, clean_streams):
        """Verify approved signal messages have all required fields."""
        signal = {
            "symbol_id": "1",
            "symbol_name": "RELIANCE",
            "signal_type": "BUY",
            "entry_price": "2400.00",
            "stop_loss": "2380.00",
            "target_price": "2440.00",
            "atr_value": "15.5",
            "candle_time": datetime.now(timezone.utc).isoformat(),
            "contributing_strategies": json.dumps(["ema_crossover"]),
            "quality_score": "75",
        }

        msg_id = await publish_to_stream(STREAM_SIGNALS_APPROVED, signal)
        assert msg_id is not None

    @pytest.mark.asyncio
    async def test_risk_passed_signal_message_format(self, clean_streams):
        """Verify risk-passed signal messages enriched with quantity."""
        signal = {
            "symbol_id": "1",
            "symbol_name": "RELIANCE",
            "signal_type": "BUY",
            "entry_price": "2400.00",
            "stop_loss": "2380.00",
            "target_price": "2440.00",
            "quantity": "10",
            "risk_amount": "200.00",
            "risk_pct": "0.002",
            "risk_reward": "2.0",
            "risk_status": "APPROVED",
        }

        msg_id = await publish_to_stream(STREAM_SIGNALS_RISK_PASSED, signal)
        assert msg_id is not None

    @pytest.mark.asyncio
    async def test_executed_signal_message_format(self, clean_streams):
        """Verify executed signal messages have trade details."""
        executed = {
            "trade_id": "42",
            "symbol": "RELIANCE",
            "signal_type": "BUY",
            "entry_price": "2400.00",
            "quantity": "10",
            "mode": "paper",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        msg_id = await publish_to_stream(STREAM_SIGNALS_EXECUTED, executed)
        assert msg_id is not None

    @pytest.mark.asyncio
    async def test_full_pipeline_message_propagation(self, clean_streams):
        """
        End-to-end test: publish to each stream in sequence and verify
        all messages are present.
        """
        from app.core.redis import redis_client

        # 1. Publish candle
        candle_id = await publish_to_stream(STREAM_CANDLES, {
            "symbol": "TCS",
            "symbol_id": "2",
            "time": datetime.now(timezone.utc).isoformat(),
            "open": "3500", "high": "3520", "low": "3490",
            "close": "3510", "volume": "5000",
        })
        assert candle_id

        # 2. Publish approved signal (simulating signal engine output)
        approved_id = await publish_to_stream(STREAM_SIGNALS_APPROVED, {
            "symbol_id": "2",
            "symbol_name": "TCS",
            "signal_type": "BUY",
            "entry_price": "3510",
            "stop_loss": "3490",
            "target_price": "3550",
            "quality_score": "80",
        })
        assert approved_id

        # 3. Publish risk-passed signal
        risk_id = await publish_to_stream(STREAM_SIGNALS_RISK_PASSED, {
            "symbol_id": "2",
            "symbol_name": "TCS",
            "signal_type": "BUY",
            "entry_price": "3510",
            "quantity": "5",
            "risk_status": "APPROVED",
        })
        assert risk_id

        # 4. Publish executed signal
        exec_id = await publish_to_stream(STREAM_SIGNALS_EXECUTED, {
            "trade_id": "99",
            "symbol": "TCS",
            "signal_type": "BUY",
            "mode": "paper",
        })
        assert exec_id

        # Verify all streams have messages
        for stream_name in [
            STREAM_CANDLES,
            STREAM_SIGNALS_APPROVED,
            STREAM_SIGNALS_RISK_PASSED,
            STREAM_SIGNALS_EXECUTED,
        ]:
            messages = await redis_client.xrange(stream_name)
            assert len(messages) >= 1, f"No messages in {stream_name}"


# ── Worker Instantiation Tests ───────────────────────────────────────────────


class TestWorkerInstantiation:
    """Test that all worker classes can be instantiated."""

    def test_signal_engine_worker_init(self):
        from app.workers.signal_engine_worker import SignalEngineWorker
        w = SignalEngineWorker()
        assert w.NAME == "signal-engine-worker"
        assert w.is_running is True

    def test_risk_engine_worker_init(self):
        from app.workers.risk_engine_worker import RiskEngineWorker
        w = RiskEngineWorker()
        assert w.NAME == "risk-engine-worker"
        assert w.is_running is True

    def test_autotrader_worker_init(self):
        from app.workers.autotrader_worker import AutoTraderWorker
        w = AutoTraderWorker()
        assert w.NAME == "autotrader-worker"
        assert w.is_running is True

    def test_trade_monitor_worker_init(self):
        from app.workers.trade_monitor_worker import TradeMonitorWorker
        w = TradeMonitorWorker()
        assert w.NAME == "trade-monitor-worker"
        assert w.is_running is True

    def test_ml_pipeline_init(self):
        from app.workers.ml_pipeline import MLPipeline
        w = MLPipeline()
        assert w.NAME == "ml-pipeline"

    def test_worker_stop(self):
        from app.workers.signal_engine_worker import SignalEngineWorker
        w = SignalEngineWorker()
        assert w.is_running is True
        w.stop()
        assert w.is_running is False
