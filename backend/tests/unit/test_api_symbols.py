import pytest
from datetime import datetime
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_list_symbols(client: AsyncClient):
    response = await client.get("/api/v1/symbols")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    if len(data) > 0:
        assert "trading_symbol" in data[0]

@pytest.mark.asyncio
async def test_add_duplicate_symbol(client: AsyncClient):
    # Add first time
    await client.post(
        "/api/v1/symbols",
        json={"trading_symbol": "RELIANCE", "exchange": "NSE"}
    )
    # Attempting to add it again should trigger a 409 Conflict.
    response = await client.post(
        "/api/v1/symbols",
        json={"trading_symbol": "RELIANCE", "exchange": "NSE"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Symbol RELIANCE already exists"

@pytest.mark.asyncio
async def test_remove_symbol_not_found(client: AsyncClient):
    # Attempting to remove a non-existent symbol ID
    response = await client.delete("/api/v1/symbols/99999")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_add_symbol_success(client: AsyncClient):
    # Add a new unique symbol
    symbol_name = f"TEST_{datetime.now().timestamp()}"
    response = await client.post(
        "/api/v1/symbols",
        json={"trading_symbol": symbol_name, "exchange": "NSE"}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["trading_symbol"] == symbol_name

@pytest.mark.asyncio
async def test_remove_symbol_success(client: AsyncClient):
    # First add a symbol to remove
    symbol_name = f"REMOVE_ME_{datetime.now().timestamp()}"
    add_response = await client.post(
        "/api/v1/symbols",
        json={"trading_symbol": symbol_name, "exchange": "NSE"}
    )
    symbol_id = add_response.json()["id"]
    
    # Now remove it
    response = await client.delete(f"/api/v1/symbols/{symbol_id}")
    assert response.status_code == 204
