import os
import urllib.request
import subprocess

PROTO_URL = "https://assets.upstox.com/feed/market-data-feed/v1/MarketDataFeed.proto"
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTO_DIR = os.path.join(BACKEND_DIR, "app", "ingestion", "proto")

def main():
    if not os.path.exists(PROTO_DIR):
        os.makedirs(PROTO_DIR)

    proto_path = os.path.join(PROTO_DIR, "MarketDataFeed.proto")
    
    print(f"Downloading Protobuf schema from {PROTO_URL}...")
    try:
        urllib.request.urlretrieve(PROTO_URL, proto_path)
    except Exception as e:
        print(f"Failed to download from primary URL: {e}")
        # Fallback URL if first one fails
        try:
            fallback = "https://raw.githubusercontent.com/upstox/api-client-python/master/MarketDataFeed.proto"
            urllib.request.urlretrieve(fallback, proto_path)
        except Exception as e2:
            print(f"Failed to download from Fallback! Please provide proto content manually. {e2}")
            return
            
    print(f"Downloaded to {proto_path}")
    print("Compiling...")
    
    # Run protoc
    cmd = [
        "python", "-m", "grpc_tools.protoc",
        f"-I{PROTO_DIR}",
        f"--python_out={PROTO_DIR}",
        proto_path
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print("Success! Generated MarketDataFeed_pb2.py in", PROTO_DIR)
        
        # Add __init__.py so it can be imported
        init_path = os.path.join(PROTO_DIR, "__init__.py")
        if not os.path.exists(init_path):
            with open(init_path, "w") as f:
                pass
    else:
        print(f"Compilation Failed:\n{result.stderr}")

if __name__ == "__main__":
    main()
