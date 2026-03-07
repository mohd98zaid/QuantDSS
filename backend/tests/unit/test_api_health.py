import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock

@pytest.mark.asyncio
@patch("app.api.routers.health.check_redis_health")
@patch("app.api.routers.health.engine")
async def test_health_check(mock_engine, mock_redis, client: AsyncClient):
    mock_redis.return_value = True
    
    # Mock engine.connect() context manager correctly
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn
    mock_conn.execute.return_value = None
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "database" in data
    assert "redis" in data

@pytest.mark.asyncio
async def test_broker_health(client: AsyncClient):
    response = await client.get("/api/v1/health/broker")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "adapter" in data
