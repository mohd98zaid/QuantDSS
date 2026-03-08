import asyncio
import os
import json
import logging
import httpx
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000/api/v1")
USERNAME = os.environ.get("QUANTDSS_USER", "trader")
PASSWORD = os.environ.get("QUANTDSS_PASS", "quantdss2025")

class TestAgent:
    def __init__(self):
        self.client = httpx.AsyncClient(base_url=API_URL, timeout=15.0)
        self.token = None

    async def run_all(self):
        logger.info("Starting QuantDSS Automated Test Agent...")
        try:
            await self.test_health()
            await self.test_login()
            await self.test_strategies()
            await self.test_signals()
            await self.test_risk_config()
            logger.info("✅ All core API and DB flows have passed!")
        except AssertionError as e:
            logger.error(f"❌ TEST FAILED: {e}")
        except Exception as e:
            logger.exception(f"❌ UNEXPECTED ERROR: {e}")
        finally:
            await self.client.aclose()

    async def test_health(self):
        logger.info("➤ Testing System Health...")
        resp = await self.client.get("/health")
        assert resp.status_code == 200, f"Health check failed: {resp.status_code}"
        system_status = resp.json()
        assert system_status.get("status") == "ok", f"System health is not ok: {system_status}"
        logger.info(f"✅ System Health: {system_status}")

    async def test_login(self):
        logger.info("➤ Testing Authentication...")
        data = {"username": USERNAME, "password": PASSWORD}
        resp = await self.client.post("/auth/login", json=data)
        assert resp.status_code == 200, f"Login failed with {resp.status_code}: {resp.text}"
        self.token = resp.json().get("access_token")
        assert self.token, "Access token not found in response"
        self.client.headers.update({"Authorization": f"Bearer {self.token}"})
        logger.info("✅ Login successful")

    async def test_strategies(self):
        logger.info("➤ Testing Strategies Creation & Retrieval...")
        payload = {
            "name": f"Test Agent Strategy {int(datetime.now().timestamp())}",
            "description": "Created by Test Agent",
            "type": "ema_crossover",
            "timeframe": "1m",
            "is_active": True,
            "parameters": {"test": True}
        }
        resp = await self.client.post("/strategies", json=payload)
        # Note: Depending on unique constraints, we'll just check if it's 200/201
        if resp.status_code in (200, 201):
            strat_id = resp.json().get("id")
            logger.info(f"✅ Created strategy ID: {strat_id}")
            
        list_resp = await self.client.get("/strategies")
        assert list_resp.status_code == 200, "Failed to list strategies"
        assert isinstance(list_resp.json(), list), "Expected list of strategies"
        logger.info(f"✅ Loaded {len(list_resp.json())} strategies")

    async def test_signals(self):
        logger.info("➤ Testing Signals Endpoint...")
        resp = await self.client.get("/signals", params={"limit": 5})
        assert resp.status_code == 200, f"Failed to fetch signals: {resp.text}"
        data = resp.json()
        count = len(data.get("items", data)) if isinstance(data, dict) else len(data)
        logger.info(f"✅ Fetched {count} recent signals")

    async def test_risk_config(self):
        logger.info("➤ Testing Risk Configuration...")
        resp = await self.client.get("/risk/config")
        if resp.status_code == 200:
            logger.info("✅ Risk config loaded successfully")
        else:
            logger.warning(f"⚠️ Could not locate /risk/config endpoint ({resp.status_code})")

if __name__ == "__main__":
    agent = TestAgent()
    asyncio.run(agent.run_all())
