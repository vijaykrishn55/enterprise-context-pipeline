import asyncio

from enterprise_context.cache import HybridCache
from enterprise_context.config import Settings


def test_cache_hit_and_expiration():
    async def run():
        settings = Settings(redis_enabled=False, redis_ttl=30)
        cache = HybridCache(settings, force_memory=True)
        await cache.set("key", {"value": "saved"})
        assert await cache.get("key") == {"value": "saved"}
        cache._memory["key"] = (0.0, '{"value":"expired"}')
        assert await cache.get("key") is None
        await cache.close()
    asyncio.run(run())
