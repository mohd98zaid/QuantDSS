import asyncio
import os
import sys

# Add backend dir to PYTHONPATH so app imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.ingestion.angel_http import AngelOneHTTPClient

async def test_angel_one():
    print(f"Checking credentials...")
    print(f"API Key present: {bool(settings.angel_api_key)}")
    print(f"Client ID: {settings.angel_client_id}")
    print(f"Password present: {bool(settings.angel_password)}")
    print(f"TOTP present: {bool(settings.angel_totp_secret)}")
    print("-" * 40)
    
    if not all([settings.angel_api_key, settings.angel_client_id, settings.angel_password, settings.angel_totp_secret]):
        print("Missing credentials! Cannot proceed with test.")
        return

    client = AngelOneHTTPClient()
    
    try:
        print("1. Attempting login (automatic within get_candles_by_symbol)...")
        # Try fetching 5min candles for RELIANCE
        candles = await client.get_candles_by_symbol("RELIANCE", "5min")
        
        print("\n2. Fetch Successful!")
        print(f"Received {len(candles)} candles.")
        if candles:
            print("\nFirst candle:")
            print(candles[0])
            print(f"\nLast candle:")
            print(candles[-1])
            
    except Exception as e:
        print(f"\n❌ ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_angel_one())
