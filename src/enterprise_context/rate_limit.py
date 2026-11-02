"""Tenant-scoped dual token-bucket rate limiting for RPM and TPM quotas."""
import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after: float, message: str = "Tenant rate limit exceeded") -> None:
        super().__init__(message)
        self.retry_after = max(1, int(retry_after + 0.999))


@dataclass
class Bucket:
    tokens: float
    updated_at: float


class InMemoryTokenBucketRateLimiter:
    def __init__(self, requests_per_minute: int, tokens_per_minute: int) -> None:
        self.rpm = requests_per_minute
        self.tpm = tokens_per_minute
        self._buckets: dict[tuple[str, str], Bucket] = {}
        self._lock = asyncio.Lock()

    def _refill(self, key: tuple[str, str], capacity: int, rate: float, now: float) -> Bucket:
        bucket = self._buckets.setdefault(key, Bucket(float(capacity), now))
        bucket.tokens = min(float(capacity), bucket.tokens + max(0.0, now - bucket.updated_at) * rate)
        bucket.updated_at = now
        return bucket

    async def check(self, tenant_id: str, token_cost: int, *, charge_request: bool = True, charge_tokens: bool = True) -> None:
        now = time.monotonic()
        async with self._lock:
            req = self._refill((tenant_id, "rpm"), self.rpm, self.rpm / 60.0, now)
            tok = self._refill((tenant_id, "tpm"), self.tpm, self.tpm / 60.0, now)
            delays = []
            if charge_request and req.tokens < 1:
                delays.append((1 - req.tokens) / (self.rpm / 60.0))
            if charge_tokens and token_cost > self.tpm:
                delays.append(60.0)
            elif charge_tokens and tok.tokens < token_cost:
                delays.append((token_cost - tok.tokens) / (self.tpm / 60.0))
            if delays:
                raise RateLimitExceeded(max(delays))
            if charge_request:
                req.tokens -= 1
            if charge_tokens:
                tok.tokens -= token_cost


logger = logging.getLogger(__name__)


_REDIS_LUA = """
local now = tonumber(ARGV[1])
local rc = tonumber(ARGV[2]); local rr = tonumber(ARGV[3]); local rq = tonumber(ARGV[4])
local tc = tonumber(ARGV[5]); local tr = tonumber(ARGV[6]); local tq = tonumber(ARGV[7])
local function read_bucket(key, capacity, rate)
  local tokens = tonumber(redis.call('HGET', key, 'tokens'))
  local updated = tonumber(redis.call('HGET', key, 'updated'))
  if not tokens then tokens = capacity end
  if not updated then updated = now end
  tokens = math.min(capacity, tokens + math.max(0, now - updated) * rate)
  return tokens
end
local rt = read_bucket(KEYS[1], rc, rr)
local tt = read_bucket(KEYS[2], tc, tr)
local wait = 0
if rt < rq then wait = math.max(wait, (rq - rt) / rr) end
if tq > tc then wait = math.max(wait, 60000)
elseif tt < tq then wait = math.max(wait, (tq - tt) / tr) end
if wait > 0 then return {0, wait} end
redis.call('HSET', KEYS[1], 'tokens', rt-rq, 'updated', now)
redis.call('HSET', KEYS[2], 'tokens', tt-tq, 'updated', now)
redis.call('PEXPIRE', KEYS[1], 120000); redis.call('PEXPIRE', KEYS[2], 120000)
return {1, 0}
"""


class TenantRateLimiter:
    """Uses an atomic Redis Lua operation when available, otherwise local buckets."""
    def __init__(self, rpm: int, tpm: int, redis_client: Any | None = None) -> None:
        self._redis = redis_client
        self._local = InMemoryTokenBucketRateLimiter(rpm, tpm)
        self.rpm, self.tpm = rpm, tpm

    async def check(self, tenant_id: str, token_cost: int, *, charge_request: bool = True, charge_tokens: bool = True) -> None:
        if self._redis is None:
            return await self._local.check(tenant_id, token_cost, charge_request=charge_request, charge_tokens=charge_tokens)
        now_ms = time.time() * 1000
        safe_tenant = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:32]
        keys = [f"ecp:rate:{{{safe_tenant}}}:rpm", f"ecp:rate:{{{safe_tenant}}}:tpm"]
        args = [now_ms, self.rpm, self.rpm / 60000, int(charge_request), self.tpm,
                self.tpm / 60000, max(0, token_cost) if charge_tokens else 0]
        try:
            allowed, wait_ms = await self._redis.eval(_REDIS_LUA, 2, *keys, *args)
            if not int(allowed):
                raise RateLimitExceeded(float(wait_ms) / 1000)
        except RateLimitExceeded:
            raise
        except Exception as exc:
            logger.warning("Redis rate limiter unavailable; using process-local quota state: %s", exc)
            self._redis = None
            await self._local.check(tenant_id, token_cost, charge_request=charge_request, charge_tokens=charge_tokens)
