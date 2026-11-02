"""Async-safe circuit breaker with a single half-open recovery probe."""
import asyncio
import time


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 10.0) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.failure_count = 0
        self.open_until = 0.0
        self._half_open_probe = False
        self._lock = asyncio.Lock()

    async def allow_request(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            if self.failure_count < self.failure_threshold:
                return True
            if now < self.open_until:
                return False
            if self._half_open_probe:
                return False
            self._half_open_probe = True
            return True

    async def record_success(self) -> None:
        async with self._lock:
            self.failure_count = 0
            self.open_until = 0.0
            self._half_open_probe = False

    async def record_failure(self) -> None:
        async with self._lock:
            self.failure_count += 1
            self._half_open_probe = False
            if self.failure_count >= self.failure_threshold:
                self.open_until = time.monotonic() + self.cooldown_seconds

    async def snapshot(self) -> dict:
        async with self._lock:
            if self.failure_count < self.failure_threshold:
                state = "closed"
            elif time.monotonic() < self.open_until or self._half_open_probe:
                state = "open"
            else:
                state = "half_open"
            return {"state": state, "failure_count": self.failure_count,
                    "open_for_seconds": round(max(0.0, self.open_until-time.monotonic()), 3)}
