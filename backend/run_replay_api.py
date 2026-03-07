import requests
import time

BASE_URL = "http://localhost:8001/api/v1"
USERNAME = "trader"
PASSWORD = "quantdss2025"

sample_csv = """timestamp,symbol,price,volume
2025-01-12 09:15:01,RELIANCE,2500,100
2025-01-12 09:15:20,RELIANCE,2501,200
2025-01-12 09:15:45,RELIANCE,2499,150
2025-01-12 09:16:05,RELIANCE,2505,300
2025-01-12 09:16:30,RELIANCE,2502,400
2025-01-12 09:17:10,RELIANCE,2510,500"""

def run_replay():
    print(f"--- Logging in as {USERNAME} ---")
    login_resp = requests.post(
        f"{BASE_URL}/auth/login",
        json={"username": USERNAME, "password": PASSWORD}
    )
    
    if login_resp.status_code != 200:
        print("Login failed!", login_resp.text)
        return
        
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("Login successful.")

    # Note: for this to work, the trading mode must be PAPER and not LIVE.
    print("--- Starting Market Replay via API ---")
    start_resp = requests.post(
        f"{BASE_URL}/replay/start",
        json={"csv_data": sample_csv, "replay_speed": 100},
        headers=headers
    )
    
    if start_resp.status_code != 200:
        print("Failed to start replay:", start_resp.text)
        return
        
    session_id = start_resp.json().get("session_id")
    print(f"Session started: {session_id}")
    
    # Monitor status
    while True:
        status_resp = requests.get(f"{BASE_URL}/replay/status", headers=headers)
        if status_resp.status_code == 200:
            status = status_resp.json()
            if not status.get("is_running"):
                print("Replay finished.")
                break
            print(f"Ticks processed: {status['metrics']['ticks_processed']}...")
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
