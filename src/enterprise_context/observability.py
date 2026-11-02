"""Small process-local telemetry registry and structured JSON logging."""
import json
import logging
import os
import time
from collections import Counter
from threading import Lock


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        item = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "tenant_id", "provider", "event", "status_code",
                    "latency_ms", "estimated_input_tokens", "processed_input_tokens",
                    "output_tokens", "provider_cached_input_tokens", "pruned_dynamic_chunks",
                    "dropped_history_messages", "context_audit", "retry_after"):
            value = getattr(record, key, None)
            if value is not None:
                item[key] = value
        if record.exc_info:
            item["exception"] = self.formatException(record.exc_info)
        return json.dumps(item, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


class Metrics:
    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._latency_total_ms = 0.0
        self._lock = Lock()

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counts[name] += amount

    def observe_latency(self, latency_ms: float) -> None:
        with self._lock:
            self._latency_total_ms += latency_ms
            self._counts["requests_completed"] += 1

    def record_generation(self, estimated_tokens: int, processed_tokens: int,
                          output_tokens: int, cached_input_tokens: int = 0,
                          failover: bool = False, pruned: bool = False) -> None:
        self.increment("estimated_input_tokens_total", estimated_tokens)
        self.increment("processed_input_tokens_total", processed_tokens)
        self.increment("output_tokens_total", output_tokens)
        if cached_input_tokens:
            self.increment("provider_prefix_cache_hits")
            self.increment("provider_cached_input_tokens", cached_input_tokens)
        if failover:
            self.increment("provider_failovers")
        if pruned:
            self.increment("contexts_pruned")

    def snapshot(self) -> dict:
        with self._lock:
            counts = dict(self._counts)
            completed = counts.get("requests_completed", 0)
            return {
                "counters": counts,
                "total_latency_ms": round(self._latency_total_ms, 2),
                "average_latency_ms": round(self._latency_total_ms / completed, 2) if completed else 0.0,
            }


_tracing_configured = False

def configure_tracing() -> None:
    """Install an OpenTelemetry SDK provider and optional OTLP HTTP exporter."""
    global _tracing_configured
    if _tracing_configured:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logging.getLogger(__name__).warning("OpenTelemetry SDK unavailable; spans are disabled")
        return
    provider = TracerProvider(resource=Resource.create({"service.name": "enterprise-context-pipeline"}))
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
    if not endpoint:
        base_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip().rstrip("/")
        endpoint = f"{base_endpoint}/v1/traces" if base_endpoint else ""
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        except ImportError:
            logging.getLogger(__name__).warning(
                "OTLP endpoint is configured but the HTTP exporter is missing; install the telemetry extra"
            )
    trace.set_tracer_provider(provider)
    _tracing_configured = True
