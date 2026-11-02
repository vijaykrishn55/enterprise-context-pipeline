import asyncio

import pytest

from enterprise_context.budget import TokenBudgetExceeded
from enterprise_context.cache import HybridCache
from enterprise_context.config import Settings
from enterprise_context.pipeline import EnterpriseContextPipeline
from enterprise_context.providers import ProviderError, ProviderResult
from enterprise_context.router import ProviderRouter
from enterprise_context.schemas import ContextPayload


class Provider:
    name = "primary-test"
    model = "test-model"

    def __init__(self):
        self.calls = 0

    async def generate(self, system_prompt, user_prompt, temperature):
        self.calls += 1
        await asyncio.sleep(0.01)
        return ProviderResult("answer: " + user_prompt.split("USER QUERY:\n")[-1],
                              self.name, self.model, 20, 5)

    async def close(self):
        pass


class FailedProvider:
    name = "primary-failed"
    model = "broken"

    async def generate(self, system_prompt, user_prompt, temperature):
        raise ProviderError("upstream unavailable", 503, retryable=True)

    async def close(self):
        pass


class FallbackProvider:
    name = "fallback-test"
    model = "fallback-model"

    async def generate(self, system_prompt, user_prompt, temperature):
        return ProviderResult("fallback answer", self.name, self.model, 12, 4)

    async def close(self):
        pass


def sample(query="What is the rule?", user="u1", tenant="t1"):
    return ContextPayload(tenant_id=tenant, user_id=user, session_id="s1",
        system_prompt="You are a careful enterprise assistant.",
        static_context=["Policy: encryption uses AES-256."],
        dynamic_context=["Document says AES-256 is required."], query=query)


def make_pipeline(primary=None, fallback=None):
    settings = Settings(redis_enabled=False, rate_limit_enabled=False,
                        max_concurrent_requests=50, provider_max_retries=0,
                        redis_ttl=20)
    cache = HybridCache(settings, force_memory=True)
    router = ProviderRouter(settings, primary=primary or Provider(),
                            fallback=fallback or FallbackProvider())
    return EnterpriseContextPipeline(settings, cache=cache, router=router)


def test_pipeline_caches_exact_request_and_isolates_tenants():
    async def run():
        pipeline = make_pipeline()
        await pipeline.startup()
        first = await pipeline.process_request(sample())
        second = await pipeline.process_request(sample())
        other_tenant = await pipeline.process_request(sample(tenant="t2"))
        assert first.cache_hit is False
        assert second.cache_hit is True
        assert second.processed_input_tokens == 0
        assert other_tenant.cache_hit is False
        assert second.response_text == first.response_text
        await pipeline.close()
    asyncio.run(run())


def test_pipeline_routes_to_fallback():
    async def run():
        pipeline = make_pipeline(FailedProvider(), FallbackProvider())
        await pipeline.startup()
        result = await pipeline.process_request(sample(query="force fallback"))
        assert result.failover_used is True
        assert result.provider_used == "fallback-test"
        await pipeline.close()
    asyncio.run(run())


def test_pipeline_rejects_context_when_mandatory_anchors_do_not_fit():
    async def run():
        pipeline = make_pipeline()
        await pipeline.startup()
        too_large = sample(query="q" * 2000)
        too_large.max_context_tokens = 128
        with pytest.raises(TokenBudgetExceeded):
            await pipeline.process_request(too_large)
        await pipeline.close()
    asyncio.run(run())


