"""
Seed script — Insert default strategies, symbols, risk config, and daily state.
Run: python -m scripts.seed_defaults
"""
import asyncio
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.daily_risk_state import DailyRiskState
from app.models.risk_config import RiskConfig
from app.models.strategy import Strategy, StrategySymbol
from app.models.symbol import Symbol

# Default NSE symbols
DEFAULT_SYMBOLS = [
    {"trading_symbol": "RELIANCE", "exchange": "NSE"},
    {"trading_symbol": "TCS", "exchange": "NSE"},
    {"trading_symbol": "INFY", "exchange": "NSE"},
    {"trading_symbol": "HDFCBANK", "exchange": "NSE"},
    {"trading_symbol": "ICICIBANK", "exchange": "NSE"},
    {"trading_symbol": "SBIN", "exchange": "NSE"},
]

# Default strategies with parameters
DEFAULT_STRATEGIES = [
    {
        "name": "EMA Crossover",
        "type": "trend_following",
        "description": "Enter when fast EMA crosses above slow EMA with volume confirmation.",
        "parameters": {
            "ema_fast": 9,
            "ema_slow": 21,
            "atr_period": 14,
            "atr_multiplier_sl": 1.5,
            "atr_multiplier_target": 3.0,
            "volume_ma_period": 20,
        },
    },
    {
        "name": "RSI Mean Reversion",
        "type": "mean_reversion",
        "description": "Enter oversold bounces in uptrend or overbought rejections in downtrend.",
        "parameters": {
            "rsi_period": 14,
            "rsi_oversold": 35,
            "rsi_overbought": 65,
            "ema_trend": 50,
            "atr_period": 14,
            "atr_multiplier_sl": 1.0,
            "risk_reward": 2.0,
        },
    },
]


async def seed():
    """Populate database with default data."""
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        # 1. Insert default risk config (singleton)
        existing_config = await session.execute(select(RiskConfig))
        if not existing_config.scalar_one_or_none():
            config = RiskConfig(
                risk_per_trade_pct=Decimal(str(settings.risk_per_trade_pct)),
                max_daily_loss_inr=Decimal(str(settings.max_daily_loss_inr)),
                max_daily_loss_pct=Decimal(str(settings.max_daily_loss_pct)),
                max_account_drawdown_pct=Decimal(str(settings.max_account_drawdown_pct)),
                cooldown_minutes=settings.cooldown_minutes,
                min_atr_pct=Decimal(str(settings.min_atr_pct)),
                max_atr_pct=Decimal(str(settings.max_atr_pct)),
                max_position_pct=Decimal(str(settings.max_position_pct)),
                max_concurrent_positions=settings.max_concurrent_positions,
            )
            session.add(config)
            print("✅ Risk config seeded")
        else:
            print("⏭️  Risk config already exists — skipping")

        # 2. Insert default symbols
        symbol_objects = {}
        for sym_data in DEFAULT_SYMBOLS:
            existing = await session.execute(
                select(Symbol).where(Symbol.trading_symbol == sym_data["trading_symbol"])
            )
            if not existing.scalar_one_or_none():
                symbol = Symbol(**sym_data)
                session.add(symbol)
                await session.flush()
                symbol_objects[sym_data["trading_symbol"]] = symbol
                print(f"✅ Symbol added: {sym_data['trading_symbol']}")
            else:
                # Get the existing symbol for mapping
                result = await session.execute(
                    select(Symbol).where(Symbol.trading_symbol == sym_data["trading_symbol"])
                )
                symbol_objects[sym_data["trading_symbol"]] = result.scalar_one()
                print(f"⏭️  Symbol exists: {sym_data['trading_symbol']} — skipping")

        # 3. Insert default strategies
        strategy_objects = []
        for strat_data in DEFAULT_STRATEGIES:
            existing = await session.execute(
                select(Strategy).where(Strategy.name == strat_data["name"])
            )
            if not existing.scalar_one_or_none():
                strategy = Strategy(**strat_data)
                session.add(strategy)
                await session.flush()
                strategy_objects.append(strategy)
                print(f"✅ Strategy added: {strat_data['name']}")
            else:
                result = await session.execute(
                    select(Strategy).where(Strategy.name == strat_data["name"])
                )
                strategy_objects.append(result.scalar_one())
                print(f"⏭️  Strategy exists: {strat_data['name']} — skipping")

        # 4. Map all symbols to both strategies
        for strategy in strategy_objects:
            for sym_name, symbol in symbol_objects.items():
                existing = await session.execute(
                    select(StrategySymbol).where(
                        StrategySymbol.strategy_id == strategy.id,
                        StrategySymbol.symbol_id == symbol.id,
                    )
                )
                if not existing.scalar_one_or_none():
                    mapping = StrategySymbol(
                        strategy_id=strategy.id,
                        symbol_id=symbol.id,
                        timeframe="1min",
                    )
                    session.add(mapping)
                    print(f"✅ Mapped: {strategy.name} ↔ {sym_name}")

        # 5. Create today's daily risk state
        today = date.today()
        existing_state = await session.execute(
            select(DailyRiskState).where(DailyRiskState.trade_date == today)
        )
        if not existing_state.scalar_one_or_none():
            state = DailyRiskState(
                trade_date=today,
                account_balance=Decimal("100000.00"),
                peak_balance=Decimal("100000.00"),
                max_daily_loss=Decimal(str(settings.max_daily_loss_inr)),
            )
            session.add(state)
            print(f"✅ Daily risk state created for {today}")
        else:
            print(f"⏭️  Daily risk state for {today} already exists — skipping")

        await session.commit()
        print("\n🎉 Seed complete!")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
