import requests
import time
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8001/api/v1"
USERNAME = "trader"
PASSWORD = "quantdss2025"

def generate_csv():
    start_time = datetime(2026, 3, 6, 9, 15, 0)
    lines = ["timestamp,symbol,price,volume"]
    price = 2500.0
    # Generate ~200 ticks over ~100 minutes of market data to fill indicator buffers
    # An uptrend to guarantee a BUY signal from VWAP/MACD strategies.
    for i in range(250):
        t_str = (start_time + timedelta(seconds=i*25)).strftime("%Y-%m-%d %H:%M:%S")
        price += 0.8  # Consistent uptrend
        vol = 500 + (i % 10)*50
        lines.append(f"{t_str},RELIANCE,{price:.2f},{vol}")
    return "\n".join(lines)

def run_replay():
    # Delay to let the browser agent get into position
    print("Waiting 10 seconds for browser agent to focus...")
    time.sleep(10)
    
    print(f"--- Logging in as {USERNAME} ---")
    try:
        login_resp = requests.post(
            f"{BASE_URL}/auth/login",
            json={"username": USERNAME, "password": PASSWORD}
        )
        login_resp.raise_for_status()
    except Exception as e:
        print("Login failed!", e)
        return
        
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("Login successful.")

    csv_data = generate_csv()
    
    print("--- Starting Market Replay via API (Rich Dataset) ---")
    try:
        start_resp = requests.post(
            f"{BASE_URL}/replay/start",
            json={"csv_data": csv_data, "replay_speed": 20}, # Replay speed 20x ensures it takes about 12 seconds to run
            headers=headers
        )
        start_resp.raise_for_status()
    except Exception as e:
        print("Failed to start replay:", e)
        return
        
    session_id = start_resp.json().get("session_id")
    print(f"Session started: {session_id}")
    
    # Monitor status
    while True:
        try:
            status_resp = requests.get(f"{BASE_URL}/replay/status", headers=headers)
            if status_resp.status_code == 200:
                status = status_resp.json()
                if not status.get("is_running"):
                    print("Replay finished.")
                    break
                print(f"Ticks processed: {status['metrics']['ticks_processed']}/{status['metrics']['total_ticks']}...")
        except Exception:
            pass
        time.sleep(1)
        
    # Get final summary
    print(f"\n--- Fetching Final Summary for {session_id} ---")
    summary_resp = requests.get(f"{BASE_URL}/replay/summary/{session_id}", headers=headers)
    if summary_resp.status_code == 200:
        print(summary_resp.json())
    else:
        print("Failed to fetch summary:", summary_resp.text)

if __name__ == "__main__":
    run_replay()
