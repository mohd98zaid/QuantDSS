import asyncio
from app.replay.market_replay_engine import market_replay_engine
from app.replay.replay_controller import ReplayController
from app.engine.trading_mode import trading_mode_controller, TradingMode
from app.core.logging import logger

# Fake CSV data for testing
sample_csv = """timestamp,symbol,price,volume
2025-01-12 09:15:01,RELIANCE,2500,100
2025-01-12 09:15:20,RELIANCE,2501,200
2025-01-12 09:15:45,RELIANCE,2499,150
2025-01-12 09:16:05,RELIANCE,2505,300
2025-01-12 09:16:30,RELIANCE,2502,400
2025-01-12 09:17:10,RELIANCE,2510,500
"""

async def run_test():
    print("--- Starting Market Replay Test ---")
    
    # Mock the TradingModeController.get_mode to return PAPER to bypass DB requirement in test
    trading_mode_controller.get_mode = lambda cfg: TradingMode.PAPER
    
    # Start Replay
    # Using 100x speed for quick test
    session_id = await ReplayController.start(sample_csv, speed=100)
    print(f"Session started: {session_id}")
    
    # Monitor status while running
    while True:
        status = ReplayController.status()
        if not status["is_running"]:
            break
        print(f"Progress: {status['metrics']['ticks_processed']} ticks processed...")
        await asyncio.sleep(0.5)
        
    final_metrics = ReplayController.stop()
    print("\n--- Final Replay Metrics ---")
    for k, v in final_metrics["metrics"].items():
        print(f"{k}: {v}")

if __name__ == "__main__":
    asyncio.run(run_test())
