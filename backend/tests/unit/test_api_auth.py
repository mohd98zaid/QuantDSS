import pytest
from httpx import AsyncClient

# Test credentials injected via fixture — not real production values
TEST_USERNAME = "test_trader"
TEST_PASSWORD = "test_password_123"


@pytest.fixture(autouse=True)
def inject_test_credentials(monkeypatch):
    """Inject test credentials into settings so auth endpoints work in tests."""
    from app.core import config
    monkeypatch.setattr(config.settings, "default_username", TEST_USERNAME)
    monkeypatch.setattr(config.settings, "default_password", TEST_PASSWORD)
    # Clear the LRU cache so the new password hash is computed with the test password
    from app.api.routers.auth import _get_hashed_password
    _get_hashed_password.cache_clear()
    yield
    _get_hashed_password.cache_clear()


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

@pytest.mark.asyncio
async def test_login_invalid_user(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "wronguser", "password": TEST_PASSWORD}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"

@pytest.mark.asyncio
async def test_login_invalid_password(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": TEST_USERNAME, "password": "wrongpassword"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"
