"""Core async context orchestration service."""
import asyncio
import time
import uuid

from .budget import ContextBudgeter
from .cache import HybridCache, cache_key
from .config import Settings
from .errors import PipelineOverloaded
from .execution import execute_cache_miss
from .observability import Metrics
from .rate_limit import TenantRateLimiter
from .response_factory import cache_hit_response
from .router import ProviderRouter
from .schemas import ContextPayload, PipelineResponse
from .tracing import traced


class EnterpriseContextPipeline:
    def __init__(self, settings: Settings | None = None, cache: HybridCache | None = None,
                 router: ProviderRouter | None = None, metrics: Metrics | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.cache = cache or HybridCache(self.settings)
        self.router = router or ProviderRouter(self.settings)
        self.metrics = metrics or Metrics()
        self.budgeter = ContextBudgeter()
        self._semaphore = asyncio.Semaphore(self.settings.max_concurrent_requests)
        self._slots = self.settings.max_concurrent_requests
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._key_locks_guard = asyncio.Lock()
        self.rate_limiter = TenantRateLimiter(self.settings.tenant_rpm_limit,
                                              self.settings.tenant_tpm_limit)

    async def startup(self) -> None:
        await self.cache.initialize()
        self.rate_limiter = TenantRateLimiter(
            self.settings.tenant_rpm_limit, self.settings.tenant_tpm_limit,
            redis_client=self.cache.redis_client,
        )

    @traced("context.process")
    async def process_request(self, payload: ContextPayload, request_id: str | None = None) -> PipelineResponse:
        started = time.perf_counter()
        request_id = request_id or uuid.uuid4().hex
        maximum = payload.max_context_tokens or self.settings.max_context_tokens
        if self.settings.rate_limit_enabled:
            maximum = min(maximum, self.settings.tenant_tpm_limit)
        compiled = self.budgeter.compile(payload, maximum)
        key = cache_key(payload, compiled.system_prefix, compiled.user_prompt,
                        self.settings.primary_provider, self.settings.primary_model)
        if self.settings.rate_limit_enabled:
            await self.rate_limiter.check(payload.tenant_id, 0, charge_tokens=False)
        cached = await self.cache.get(key)
        if cached is not None:
            return cache_hit_response(cached, compiled, request_id, payload.tenant_id,
                                      started, self.metrics, self.cache.backend_name)
        lock = await self._get_key_lock(key)
        try:
            async with lock:
                cached = await self.cache.get(key)
                if cached is not None:
                    return cache_hit_response(cached, compiled, request_id, payload.tenant_id,
                                              started, self.metrics, self.cache.backend_name)
                if self.settings.rate_limit_enabled:
                    await self.rate_limiter.check(payload.tenant_id, compiled.estimated_tokens,
                                                  charge_request=False, charge_tokens=True)
                return await execute_cache_miss(payload, compiled, key, request_id, started,
                    self.router, self.cache, self._semaphore, self.metrics)
        finally:
            async with self._key_locks_guard:
                if not lock.locked() and self._key_locks.get(key) is lock:
                    self._key_locks.pop(key, None)

    async def _get_key_lock(self, key: str) -> asyncio.Lock:
        async with self._key_locks_guard:
            return self._key_locks.setdefault(key, asyncio.Lock())

    async def health(self) -> dict:
        return {"status": "ok", "cache_backend": self.cache.backend_name,
                "concurrency_limit": self._slots, "router": await self.router.health()}

    async def close(self) -> None:
        await self.router.close()
        await self.cache.close()
