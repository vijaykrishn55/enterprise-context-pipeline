"""Mock and OpenAI-compatible asynchronous LLM provider adapters."""
import asyncio
import os
from dataclasses import dataclass
from typing import Protocol

import httpx

from .budget import estimate_tokens
from .config import Settings


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None,
                 retryable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class ProviderResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


class LLMProvider(Protocol):
    name: str
    model: str

    async def generate(self, system_prompt: str, user_prompt: str,
                       temperature: float) -> ProviderResult: ...
    async def close(self) -> None: ...


class MockProvider:
    """Deterministic provider for offline tests and local demonstrations."""
    def __init__(self, name: str, model: str, fail_when_requested: bool = False) -> None:
        self.name, self.model = name, model
        self.fail_when_requested = fail_when_requested

    async def generate(self, system_prompt: str, user_prompt: str,
                       temperature: float) -> ProviderResult:
        await asyncio.sleep(0.01)
        if self.fail_when_requested and os.getenv("SIMULATE_FAILURE", "false").lower() == "true":
            raise ProviderError("Simulated upstream HTTP 503", 503, retryable=True)
        query = user_prompt.split("USER QUERY:\n")[-1].strip()
        text = f"Mock response for: {query[:240]}"
        return ProviderResult(text, self.name, self.model,
                              estimate_tokens(system_prompt + user_prompt), estimate_tokens(text))

    async def close(self) -> None:
        return None


class OpenAICompatibleProvider:
    def __init__(self, name: str, model: str, base_url: str, api_key: str,
                 timeout_seconds: float) -> None:
        if not base_url or not api_key:
            raise ValueError(f"Provider '{name}' requires a base URL and API key.")
        self.name, self.model = name, model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def generate(self, system_prompt: str, user_prompt: str,
                       temperature: float) -> ProviderResult:
        try:
            response = await self._client.post(self._url, headers=self._headers, json={
                "model": self.model,
                "messages": [{"role": "system", "content": system_prompt},
                             {"role": "user", "content": user_prompt}],
                "temperature": temperature,
                "max_tokens": 1024,
            })
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(f"Provider transport error: {type(exc).__name__}", retryable=True) from exc
        if response.is_error:
            status = response.status_code
            retryable = status in {408, 409, 429} or status >= 500
            raise ProviderError(f"Upstream provider returned HTTP {status}", status, retryable)
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Provider response did not match the expected chat-completions format") from exc
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("Provider returned an empty response")
        usage = data.get("usage") or {}
        prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        cached_tokens = prompt_details.get("cached_tokens", usage.get("cache_read_input_tokens", 0)) or 0
        return ProviderResult(text, self.name, self.model,
                              int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                              int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
                              int(cached_tokens))

    async def close(self) -> None:
        await self._client.aclose()


def build_provider(settings: Settings, primary: bool) -> LLMProvider:
    if primary:
        name, model = settings.primary_provider, settings.primary_model
        base_url, api_key = settings.primary_base_url, settings.primary_api_key
    else:
        name, model = settings.fallback_provider, settings.fallback_model
        base_url, api_key = settings.fallback_base_url, settings.fallback_api_key
    if name == "mock":
        return MockProvider(name, model, fail_when_requested=primary)
    if name in {"openai", "openai-compatible", "groq"}:
        return OpenAICompatibleProvider(name, model, base_url, api_key,
                                        settings.provider_timeout_seconds)
    raise ValueError(f"Unsupported provider '{name}'. Use mock, openai-compatible, or groq.")
