import asyncio
import pytest

from enterprise_context.rate_limit import InMemoryTokenBucketRateLimiter, RateLimitExceeded


def test_rate_limiter_throttles_per_tenant():
    async def run():
        limiter = InMemoryTokenBucketRateLimiter(requests_per_minute=1, tokens_per_minute=100)
        await limiter.check("tenant-a", 20, charge_tokens=True)
        with pytest.raises(RateLimitExceeded):
            await limiter.check("tenant-a", 0, charge_tokens=False)
        # Separate tenants receive independent buckets.
        await limiter.check("tenant-b", 20, charge_tokens=True)
    asyncio.run(run())


def test_token_limit_can_be_charged_without_consuming_request_slot():
    async def run():
        limiter = InMemoryTokenBucketRateLimiter(requests_per_minute=5, tokens_per_minute=10)
        await limiter.check("tenant-a", 5, charge_request=True, charge_tokens=False)
        await limiter.check("tenant-a", 10, charge_request=False, charge_tokens=True)
        with pytest.raises(RateLimitExceeded):
            await limiter.check("tenant-a", 1, charge_request=False, charge_tokens=True)
    asyncio.run(run())
