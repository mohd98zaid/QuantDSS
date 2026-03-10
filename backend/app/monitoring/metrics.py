"""
Observability and Telemetry

Exposes Prometheus metrics and structured logging formatting for operational monitoring.
"""
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
import prometheus_client
from prometheus_client import Gauge, Counter, Histogram
import json
from datetime import datetime, timezone

# --- Prometheus Metrics Definitions ---

# 1. Execution Latency (ms)
EXECUTION_LATENCY = Histogram(
    'execution_latency_ms', 
    'Latency of trade execution from signal generation to broker acknowledgment',
    buckets=[10, 50, 100, 250, 500, 1000, 5000]
)

# 2. Risk Utilization (%)
RISK_UTILIZATION = Gauge(
    'risk_utilization_pct',
    'Percentage of max daily risk currently allocated or lost'
)

# 3. Worker Health Status (1 = healthy, 0 = dead)
WORKER_HEALTH = Gauge(
    'worker_health_status',
    'Health status of distributed workers',
    ['worker_name']
)

# 4. Redis Stream Lag (ms or count)
REDIS_STREAM_LAG = Gauge(
    'redis_stream_lag',
    'Number of pending messages in the PEL for primary consumer groups',
    ['stream_name', 'group_name']
)

# 5. Broker API Errors
BROKER_API_ERRORS = Counter(
    'broker_api_errors',
    'Count of consecutive or total broker API failures',
    ['broker_name', 'endpoint']
)

# 6. Open Position Count
OPEN_POSITION_COUNT = Gauge(
    'open_position_count',
    'Total number of open positions currently held in Live/Paper'
)

# --- FastAPI Router Endpoint ---
metrics_router = APIRouter(tags=["observability"])

@metrics_router.get("/metrics", response_class=PlainTextResponse)
def get_metrics():
    """
    Exposes the recorded metrics to Prometheus scraping.
    """
    return prometheus_client.generate_latest()


# --- Structured Logging ---

def emit_structured_log(worker_name: str, trace_id: str, event_type: str, data: dict = None):
    """
    Emits a deterministic structured JSON log for workers to utilize in centralized logging (e.g. ELK/Datadog).
    """
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "worker_name": worker_name,
        "trace_id": trace_id,
        "event_type": event_type,
        "data": data or {}
    }
    # We output to standard out so container orchestrators can ingest it properly
    print(json.dumps(log_entry))
