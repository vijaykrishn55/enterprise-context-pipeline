import asyncio

from enterprise_context.circuit_breaker import CircuitBreaker


def test_breaker_opens_and_allows_one_recovery_probe():
    async def run():
        breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.01)
        assert await breaker.allow_request()
        for _ in range(3):
            await breaker.record_failure()
        assert not await breaker.allow_request()
        await asyncio.sleep(0.02)
        assert await breaker.allow_request()
        assert not await breaker.allow_request()
        await breaker.record_success()
        assert (await breaker.snapshot())["state"] == "closed"
    asyncio.run(run())
