import asyncio
import sys
import os
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))

from app.workers.autotrader_worker import AutoTraderWorker
from app.models.risk_config import RiskConfig
from app.models.auto_trade_config import AutoTradeConfig

class MockDBEntry:
    def __init__(self, value):
        self.value = value
    def scalar_one_or_none(self):
        return self.value
    def scalar_one(self):
        return self.value

class MockDB:
    async def execute(self, stmt):
        # We need to mock different responses based on the statement
        stmt_str = str(stmt).lower()
        if "riskconfig" in stmt_str:
            rc = RiskConfig(paper_balance=100000.0)
            return MockDBEntry(rc)
        if "autotradeconfig" in stmt_str:
            ac = AutoTradeConfig(max_open_positions=5, capital_per_trade=10000.0, mode="paper")
            return MockDBEntry(ac)
        if "papertrade" in stmt_str and "count" in stmt_str:
            return MockDBEntry(5) # Simulate 5 active paper trades
        if "livetrade" in stmt_str and "count" in stmt_str:
            return MockDBEntry(0) # Simulate 0 active live trades
            
        return MockDBEntry(None)
    
    def add(self, entity):
        pass

class TestAuditFixes(unittest.IsolatedAsyncioTestCase):
    
    @patch('app.workers.autotrader_worker.async_session_factory')
    @patch('app.workers.autotrader_worker.AutoTraderWorker._execute_paper_trade')
    @patch('app.workers.autotrader_worker.check_stale_signal', create=True)
    @patch('app.core.redis.redis_client', create=True)
    async def test_max_open_positions(self, mock_redis, mock_stale, mock_exec, mock_db):
        """Test FIX 2: Max Open Positions Validation"""
        # Setup mocks
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__.return_value = mock_session
        
        # Override the db.execute within the session context
        mock_session.execute = AsyncMock(side_effect=MockDB().execute)
        
        mock_stale.return_value = False
        mock_redis.set = AsyncMock(return_value=True)
        
        worker = AutoTraderWorker()
        signal_data = {
            "symbol_name": "TEST",
            "signal_type": "BUY",
            "entry_price": "100.0",
            "stop_loss": "90.0",
            "target_price": "120.0",
            "quantity": "0",
            "_trace_id": "trace_test",
            "symbol_id": "1",
            "is_replay": "true"
        }
        
        # Act
        await worker._handle_signal("msg_1", signal_data)
        
        # Assert
        # The execute method should not have been called because max positions (5) was reached
        mock_exec.assert_not_called()
        print("TEST 2 PASSED: Trade blocked due to max open positions.")

    @patch('app.workers.autotrader_worker.async_session_factory')
    @patch('app.workers.autotrader_worker.AutoTraderWorker._execute_paper_trade')
    @patch('app.workers.autotrader_worker.check_stale_signal', create=True)
    @patch('app.core.redis.redis_client', create=True)
    async def test_entry_price_validation(self, mock_redis, mock_stale, mock_exec, mock_db):
        """Test FIX 3: Entry Price Validation"""
        # Setup mocks
        worker = AutoTraderWorker()
        mock_redis.set = AsyncMock(return_value=True)
        
        signal_data = {
            "symbol_name": "TEST",
            "signal_type": "BUY",
            "entry_price": "0.0", # <--- INVALID
            "stop_loss": "0.0",
            "target_price": "0.0",
            "quantity": "0",
            "_trace_id": "trace_test",
            "symbol_id": "1",
            "is_replay": "true"
        }
        
        mock_stale.return_value = False
        
        # We don't even need to mock DB heavily because it should fail before DB calls
        # Act
        await worker._handle_signal("msg_1", signal_data)
        
        # Assert
        mock_exec.assert_not_called()
        print("TEST 3 PASSED: Zero entry price gracefully rejected.")

if __name__ == '__main__':
    unittest.main()
