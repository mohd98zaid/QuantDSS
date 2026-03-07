import pytest
from datetime import datetime
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_list_strategies(client: AsyncClient):
    response = await client.get("/api/v1/strategies")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    if len(data) > 0:
        assert "name" in data[0]

@pytest.mark.asyncio
async def test_update_strategy_not_found(client: AsyncClient):
    response = await client.put(
        "/api/v1/strategies/999",
        json={"is_active": True, "parameters": {}}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Strategy not found"

@pytest.mark.asyncio
async def test_add_symbol_to_strategy_not_found(client: AsyncClient):
    # Strategy 999 does not exist. Should return 404 now.
    response = await client.post(
        "/api/v1/strategies/999/symbols",
        json={"symbol_id": 1, "timeframe": "1d"}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Strategy not found"

@pytest.mark.asyncio
async def test_update_strategy_success(client: AsyncClient, db):
    """Seed a strategy then update it — no dependency on pre-existing DB rows."""
    from app.models.strategy import Strategy

    # Seed a strategy directly into the test DB
    strategy = Strategy(name="CI Test Strategy", type="ema_crossover", parameters={"ema_fast": 9})
    db.add(strategy)
    await db.flush()
    await db.refresh(strategy)
    strategy_id = strategy.id

    response = await client.put(
        f"/api/v1/strategies/{strategy_id}",
        json={"is_active": True, "parameters": {"test_param": 1.23}}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["is_active"] is True
    assert data["parameters"]["test_param"] == 1.23

@pytest.mark.asyncio
async def test_add_symbol_to_strategy_success(client: AsyncClient, db):
    """Seed a strategy + symbol, then link them — no dependency on pre-existing DB rows."""
    from app.models.strategy import Strategy
    from app.models.symbol import Symbol
    from datetime import datetime

    # Seed strategy
    strategy = Strategy(name="CI Link Strategy", type="rsi_mean_reversion", parameters={})
    db.add(strategy)
    await db.flush()
    await db.refresh(strategy)
    strategy_id = strategy.id

    # Seed a unique symbol via HTTP (reuse existing endpoint)
    unique_symbol = f"STRAT_TEST_{datetime.now().timestamp()}"
    sym_res = await client.post(
        "/api/v1/symbols",
        json={"trading_symbol": unique_symbol, "exchange": "NSE"}
    )
    assert sym_res.status_code == 201
    symbol_id = sym_res.json()["id"]

    response = await client.post(
        f"/api/v1/strategies/{strategy_id}/symbols",
        json={"symbol_id": symbol_id, "timeframe": "1h"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["strategy_id"] == strategy_id
    assert data["symbol_id"] == symbol_id
