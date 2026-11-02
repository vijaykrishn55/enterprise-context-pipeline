"""TTL response cache with Redis preference and graceful in-memory fallback."""
import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from .config import Settings
from .schemas import ContextPayload

logger = logging.getLogger(__name__)


class HybridCache:
    def __init__(self, settings: Settings, force_memory: bool = False) -> None:
        self.ttl = settings.redis_ttl
        self._memory: dict[str, tuple[float, str]] = {}
        self._lock = asyncio.Lock()
        self._redis: Any | None = None
        self._redis_available = False
        if settings.redis_enabled and not force_memory:
            try:
                if settings.redis_cluster_enabled:
                    from redis.asyncio.cluster import RedisCluster
                    client_type = RedisCluster
                else:
                    from redis.asyncio import Redis
                    client_type = Redis
                self._redis = client_type.from_url(settings.redis_url, decode_responses=True,
                                                   socket_connect_timeout=0.4, socket_timeout=0.4)
            except ImportError:
                logger.warning("redis-py not installed; using in-memory cache")

    @property
    def redis_client(self) -> Any | None:
        return self._redis if self._redis_available else None

    @property
    def backend_name(self) -> str:
        return "redis" if self._redis_available else "memory"

    async def initialize(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.ping()
            self._redis_available = True
            logger.info("Redis cache connected", extra={"event": "redis_connected"})
        except Exception as exc:
            self._redis_available = False
            logger.warning("Redis unavailable; using in-memory fallback: %s", exc)

    async def get(self, key: str) -> dict | None:
        if self._redis_available:
            try:
                value = await self._redis.get(key)
                return json.loads(value) if value else None
            except Exception as exc:
                self._redis_available = False
                logger.warning("Redis read failed; falling back to memory: %s", exc)
        now = time.monotonic()
        async with self._lock:
            entry = self._memory.get(key)
            if entry is None:
                return None
            expires_at, encoded = entry
            if now >= expires_at:
                self._memory.pop(key, None)
                return None
            return json.loads(encoded)

    async def set(self, key: str, value: dict) -> None:
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        if self._redis_available:
            try:
                await self._redis.set(key, encoded, ex=self.ttl)
                return
            except Exception as exc:
                self._redis_available = False
                logger.warning("Redis write failed; storing in memory: %s", exc)
        async with self._lock:
            self._memory[key] = (time.monotonic() + self.ttl, encoded)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()


def cache_key(payload: ContextPayload, system_prefix: str, user_prompt: str,
              provider: str, model: str) -> str:
    """Hash all security and generation dimensions to avoid cross-tenant cache leaks."""
    parts = {
        "tenant_id": payload.tenant_id,
        "user_id": payload.user_id,
        "session_id": payload.session_id,
        "provider": provider,
        "model": model,
        "temperature": payload.temperature,
        "system_prefix": system_prefix,
        "user_prompt": user_prompt,
    }
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "ecp:response:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
