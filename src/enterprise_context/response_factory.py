"""Response construction shared by the request and cache-hit paths."""
import logging
import time

from .budget import CompiledContext
from .observability import Metrics
from .schemas import PipelineResponse

logger = logging.getLogger(__name__)


def cache_hit_response(cached: dict, compiled: CompiledContext, request_id: str,
                       tenant_id: str, started: float, metrics: Metrics,
                       cache_backend: str) -> PipelineResponse:
    latency = (time.perf_counter() - started) * 1000
    metrics.increment("cache_hits")
    metrics.increment("estimated_input_tokens_saved", compiled.estimated_tokens)
    metrics.observe_latency(latency)
    logger.info("Context response cache hit", extra={"request_id": request_id,
                "tenant_id": tenant_id, "event": "cache_hit",
                "latency_ms": round(latency, 2),
                "estimated_input_tokens": compiled.estimated_tokens})
    return PipelineResponse(
        request_id=request_id, response_text=cached["response_text"], cache_hit=True,
        latency_ms=round(latency, 2), estimated_input_tokens=compiled.estimated_tokens,
        processed_input_tokens=0, output_tokens=0, provider_cached_input_tokens=0,
        model_used=cached["model_used"], provider_used=cached["provider_used"],
        failover_used=cached.get("failover_used", False),
        pruned_dynamic_chunks=compiled.pruned_dynamic_chunks,
        dropped_history_messages=compiled.dropped_history_messages,
        cache_backend=cache_backend,
    )
