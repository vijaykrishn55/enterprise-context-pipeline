import asyncio

import pytest

from enterprise_context.providers import OpenAICompatibleProvider, ProviderError


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self.is_error = status_code >= 400
        self._body = body or {}

    def json(self):
        return self._body


class FakeHttpClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def post(self, url, headers, json):
        self.calls.append((url, headers, json))
        return self.response

    async def aclose(self):
        pass


def test_openai_compatible_adapter_parses_usage_and_cached_tokens():
    async def run():
        provider = OpenAICompatibleProvider("openai-compatible", "test-model",
                                            "https://example.test/v1", "fake-key", 1)
        client = FakeHttpClient(FakeResponse(body={
            "choices": [{"message": {"content": "generated answer"}}],
            "usage": {"prompt_tokens": 80, "completion_tokens": 10,
                      "prompt_tokens_details": {"cached_tokens": 32}},
        }))
        provider._client = client
        result = await provider.generate("system", "query", 0.1)
        assert client.calls[0][0] == "https://example.test/v1/chat/completions"
        assert result.text == "generated answer"
        assert result.input_tokens == 80
        assert result.output_tokens == 10
        assert result.cached_input_tokens == 32
        await provider.close()
    asyncio.run(run())


def test_openai_compatible_adapter_marks_429_retryable():
    async def run():
        provider = OpenAICompatibleProvider("groq", "test-model",
                                            "https://example.test/v1", "fake-key", 1)
        provider._client = FakeHttpClient(FakeResponse(status_code=429))
        with pytest.raises(ProviderError) as error:
            await provider.generate("system", "query", 0.1)
        assert error.value.status_code == 429
        assert error.value.retryable is True
        await provider.close()
    asyncio.run(run())
