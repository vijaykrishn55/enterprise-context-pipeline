"""Primary/fallback router with retries, circuit breakers, and bounded timeouts."""
import asyncio
import logging
from dataclasses import dataclass

from .circuit_breaker import CircuitBreaker
from .config import Settings
from .providers import LLMProvider, ProviderError, ProviderResult, build_provider
from .tracing import traced

logger = logging.getLogger(__name__)


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DispatchResult:
    result: ProviderResult
    failover_used: bool
    attempts: int


class ProviderRouter:
    def __init__(self, settings: Settings, primary: LLMProvider | None = None,
                 fallback: LLMProvider | None = None) -> None:
        self.settings = settings
        self.primary = primary or build_provider(settings, primary=True)
        self.fallback = fallback or build_provider(settings, primary=False)
        self.primary_breaker = CircuitBreaker(3, 10.0)
        self.fallback_breaker = CircuitBreaker(3, 5.0)

    @traced("llm.route")
    async def generate(self, system_prompt: str, user_prompt: str,
                       temperature: float) -> DispatchResult:
        attempts = 0
        if await self.primary_breaker.allow_request():
            try:
                result, used = await self._call(self.primary, system_prompt, user_prompt, temperature)
                attempts += used
                await self.primary_breaker.record_success()
                return DispatchResult(result, False, attempts)
            except ProviderUnavailable as exc:
                attempts += getattr(exc, "attempts", 1)
                await self.primary_breaker.record_failure()
                logger.warning("Primary provider failed; activating fallback", extra={"provider": self.primary.name, "event": "provider_failover"})
        else:
            logger.warning("Primary circuit breaker is open; skipping upstream", extra={"provider": self.primary.name, "event": "circuit_open"})
        if not await self.fallback_breaker.allow_request():
            raise ProviderUnavailable("Primary and fallback circuits are unavailable.")
        try:
            result, used = await self._call(self.fallback, system_prompt, user_prompt, temperature)
            attempts += used
            await self.fallback_breaker.record_success()
            return DispatchResult(result, True, attempts)
        except ProviderUnavailable:
            await self.fallback_breaker.record_failure()
            raise

    async def _call(self, provider: LLMProvider, system_prompt: str, user_prompt: str,
                    temperature: float) -> tuple[ProviderResult, int]:
        total_attempts = self.settings.provider_max_retries + 1
        for attempt in range(total_attempts):
            try:
                result = await asyncio.wait_for(
                    provider.generate(system_prompt, user_prompt, temperature),
                    timeout=self.settings.provider_timeout_seconds,
                )
                return result, attempt + 1
            except ProviderError as exc:
                if not exc.retryable or attempt + 1 >= total_attempts:
                    error = ProviderUnavailable(str(exc))
                    error.attempts = attempt + 1
                    raise error from exc
            except asyncio.TimeoutError as exc:
                if attempt + 1 >= total_attempts:
                    error = ProviderUnavailable("Upstream provider timed out.")
                    error.attempts = attempt + 1
                    raise error from exc
            await asyncio.sleep(self.settings.retry_base_seconds * (2 ** attempt))
        raise ProviderUnavailable("Provider retry policy exhausted.")

    async def close(self) -> None:
        await self.primary.close()
        if self.fallback is not self.primary:
            await self.fallback.close()

    async def health(self) -> dict:
        return {"primary_circuit": await self.primary_breaker.snapshot(),
                "fallback_circuit": await self.fallback_breaker.snapshot(),
                "primary_provider": self.primary.name, "fallback_provider": self.fallback.name}
