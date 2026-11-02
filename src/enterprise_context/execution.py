"""Provider dispatch and successful cache-miss response handling."""
import asyncio
import logging
import time

from .budget import CompiledContext, estimate_tokens
from .cache import HybridCache
from .errors import PipelineOverloaded
from .observability import Metrics
from .router import ProviderRouter
from .schemas import ContextPayload, PipelineResponse

logger = logging.getLogger(__name__)


async def execute_cache_miss(payload: ContextPayload, compiled: CompiledContext, key: str,
                             request_id: str, started: float, router: ProviderRouter,
                             cache: HybridCache, semaphore: asyncio.Semaphore,
                             metrics: Metrics) -> PipelineResponse:
    metrics.increment("cache_misses")
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=0.05)
    except asyncio.TimeoutError as exc:
        metrics.increment("overloaded_requests")
        raise PipelineOverloaded("The pipeline is at its configured concurrency limit.") from exc
    try:
        dispatch = await router.generate(compiled.system_prefix, compiled.user_prompt,
                                         payload.temperature)
    finally:
        semaphore.release()

    result = dispatch.result
    output_tokens = result.output_tokens or estimate_tokens(result.text)
    cached_data = {"response_text": result.text, "model_used": result.model,
        "provider_used": result.provider, "failover_used": dispatch.failover_used,
        "output_tokens": output_tokens, "provider_cached_input_tokens": result.cached_input_tokens}
    await cache.set(key, cached_data)
    latency = (time.perf_counter() - started) * 1000
    processed_tokens = result.input_tokens or compiled.estimated_tokens
    metrics.record_generation(compiled.estimated_tokens, processed_tokens, output_tokens,
                              result.cached_input_tokens, dispatch.failover_used, bool(compiled.audit))
    metrics.observe_latency(latency)
    if compiled.audit:
        logger.info("Context budget enforced", extra={"request_id": request_id,
            "tenant_id": payload.tenant_id, "event": "context_pruned",
            "pruned_dynamic_chunks": compiled.pruned_dynamic_chunks,
            "dropped_history_messages": compiled.dropped_history_messages,
            "estimated_input_tokens": compiled.estimated_tokens,
            "context_audit": ";".join(compiled.audit)})
    logger.info("Context request completed", extra={"request_id": request_id,
        "tenant_id": payload.tenant_id, "provider": result.provider,
        "event": "request_completed", "latency_ms": round(latency, 2),
        "estimated_input_tokens": compiled.estimated_tokens,
        "processed_input_tokens": processed_tokens, "output_tokens": output_tokens,
        "provider_cached_input_tokens": result.cached_input_tokens})
    return PipelineResponse(request_id=request_id, response_text=result.text, cache_hit=False,
        latency_ms=round(latency, 2), estimated_input_tokens=compiled.estimated_tokens,
        processed_input_tokens=processed_tokens, output_tokens=output_tokens,
        provider_cached_input_tokens=result.cached_input_tokens, model_used=result.model,
        provider_used=result.provider, failover_used=dispatch.failover_used,
        pruned_dynamic_chunks=compiled.pruned_dynamic_chunks,
        dropped_history_messages=compiled.dropped_history_messages,
        cache_backend=cache.backend_name)
