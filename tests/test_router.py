import asyncio

from enterprise_context.config import Settings
from enterprise_context.providers import ProviderError, ProviderResult
from enterprise_context.router import ProviderRouter


class FlakyProvider:
    name = "primary-test"
    model = "p-model"

    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    async def generate(self, system_prompt, user_prompt, temperature):
        self.calls += 1
        if self.calls <= self.failures:
            raise ProviderError("temporary error", 503, retryable=True)
        return ProviderResult("primary answer", self.name, self.model, 10, 3)

    async def close(self):
        pass


class HealthyProvider:
    name = "fallback-test"
    model = "f-model"

    async def generate(self, system_prompt, user_prompt, temperature):
        return ProviderResult("fallback answer", self.name, self.model, 10, 3)

    async def close(self):
        pass


def test_router_retries_transient_failure():
    async def run():
        settings = Settings(redis_enabled=False, rate_limit_enabled=False,
                            provider_max_retries=1, retry_base_seconds=0.001)
        primary = FlakyProvider(failures=1)
        router = ProviderRouter(settings, primary=primary, fallback=HealthyProvider())
        result = await router.generate("sys", "query", 0.1)
        assert result.result.text == "primary answer"
        assert result.failover_used is False
        assert result.attempts == 2
        await router.close()
    asyncio.run(run())


def test_router_fails_over_after_primary_failure():
    async def run():
        settings = Settings(redis_enabled=False, rate_limit_enabled=False,
                            provider_max_retries=0)
        router = ProviderRouter(settings, primary=FlakyProvider(failures=10),
                                fallback=HealthyProvider())
        result = await router.generate("sys", "query", 0.1)
        assert result.result.provider == "fallback-test"
        assert result.failover_used is True
        await router.close()
    asyncio.run(run())


def test_router_opens_primary_circuit_after_three_failed_requests():
    async def run():
        settings = Settings(redis_enabled=False, rate_limit_enabled=False,
                            provider_max_retries=0)
        primary = FlakyProvider(failures=100)
        router = ProviderRouter(settings, primary=primary, fallback=HealthyProvider())
        for index in range(3):
            result = await router.generate("sys", f"query-{index}", 0.1)
            assert result.failover_used is True
        assert (await router.primary_breaker.snapshot())["state"] == "open"
        calls_before = primary.calls
        result = await router.generate("sys", "query-four", 0.1)
        assert result.failover_used is True
        assert primary.calls == calls_before
        await router.close()
    asyncio.run(run())
