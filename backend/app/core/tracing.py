import os
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from app.core.logging import logger

def init_tracing(service_name: str, worker_id: str = ""):
    """Initialize OpenTelemetry tracing for the service."""
    # Attempt to pull configuration from environment
    otlp_endpoint = os.getenv("OTLP_ENDPOINT", "http://localhost:4317")
    enable_tracing = os.getenv("ENABLE_TRACING", "false").lower() == "true"

    if not enable_tracing:
        return

    attributes = {"service.name": service_name}
    if worker_id:
        attributes["worker.id"] = worker_id

    try:
        resource = Resource(attributes=attributes)
        provider = TracerProvider(resource=resource)
        
        # OTLP grpc exporter
        processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True))
        provider.add_span_processor(processor)
        
        trace.set_tracer_provider(provider)
        logger.info(f"Initialized OpenTelemetry tracing for {service_name} at {otlp_endpoint}")
    except Exception as e:
        logger.error(f"Failed to initialize OpenTelemetry tracing: {e}")
