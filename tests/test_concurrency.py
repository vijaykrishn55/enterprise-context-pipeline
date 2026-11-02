import asyncio

from enterprise_context.cache import HybridCache
from enterprise_context.config import Settings
from enterprise_context.pipeline import EnterpriseContextPipeline
from enterprise_context.providers import ProviderResult
from enterprise_context.router import ProviderRouter
from enterprise_context.schemas import ContextPayload


class CountingProvider:
    name = "primary-test"
    model = "test-model"

    def __init__(self):
        self.calls = 0

    async def generate(self, system_prompt, user_prompt, temperature):
        self.calls += 1
        await asyncio.sleep(0.01)
        return ProviderResult("answer", self.name, self.model, 20, 5)

    async def close(self):
        pass


def sample(query="What is the rule?", user="u1"):
    return ContextPayload(user_id=user, session_id="s1", tenant_id="t1",
        system_prompt="You are an enterprise assistant.", query=query)


def build_pipeline(provider):
    settings = Settings(redis_enabled=False, rate_limit_enabled=False,
                        max_concurrent_requests=50, provider_max_retries=0)
    cache = HybridCache(settings, force_memory=True)
    router = ProviderRouter(settings, primary=provider, fallback=provider)
    return EnterpriseContextPipeline(settings, cache=cache, router=router)


def test_pipeline_handles_concurrent_requests():
    async def run():
        pipeline = build_pipeline(CountingProvider())
        await pipeline.startup()
        results = await asyncio.gather(*[
            pipeline.process_request(sample(query=f"question {i}", user=f"u{i}"))
            for i in range(40)
        ])
        assert len(results) == 40
        assert all(result.response_text for result in results)
        await pipeline.close()
    asyncio.run(run())


def test_identical_concurrent_requests_share_one_upstream_call():
    async def run():
        provider = CountingProvider()
        pipeline = build_pipeline(provider)
        await pipeline.startup()
        results = await asyncio.gather(*[pipeline.process_request(sample()) for _ in range(30)])
        assert len(results) == 30
        assert provider.calls == 1
        counters = pipeline.metrics.snapshot()["counters"]
        assert counters["cache_misses"] == 1
        assert counters["cache_hits"] == 29
        await pipeline.close()
    asyncio.run(run())
