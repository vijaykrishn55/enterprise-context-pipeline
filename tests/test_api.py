from fastapi.testclient import TestClient

from enterprise_context.api import create_app
from enterprise_context.config import Settings


def test_api_process_health_and_metrics():
    settings = Settings(redis_enabled=False, rate_limit_enabled=False,
                        max_concurrent_requests=10)
    app = create_app(settings)
    payload = {
        "tenant_id": "org-1", "user_id": "u1", "session_id": "s1",
        "system_prompt": "You are an enterprise assistant.",
        "static_context": ["Policy says use AES-256."],
        "dynamic_context": ["Relevant context."], "query": "Which encryption standard?",
    }
    with TestClient(app) as client:
        first = client.post("/v1/context/process", json=payload,
                            headers={"x-request-id": "req-test-1"})
        assert first.status_code == 200
        assert first.headers["x-request-id"] == "req-test-1"
        assert first.json()["cache_hit"] is False
        assert first.headers["x-context-cache"] == "MISS"
        second = client.post("/v1/context/process", json=payload)
        assert second.status_code == 200
        assert second.json()["cache_hit"] is True
        assert second.headers["x-context-cache"] == "HIT"
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/metrics").json()["counters"]["cache_hits"] == 1


def test_api_validates_bad_payload():
    app = create_app(Settings(redis_enabled=False, rate_limit_enabled=False))
    with TestClient(app) as client:
        response = client.post("/v1/context/process", json={"query": "missing required fields"})
        assert response.status_code == 422


def test_api_rate_limit_returns_retry_after():
    settings = Settings(redis_enabled=False, rate_limit_enabled=True,
                        tenant_rpm_limit=1, tenant_tpm_limit=10000)
    app = create_app(settings)
    payload = {
        "tenant_id": "org-limited", "user_id": "u1", "session_id": "s1",
        "system_prompt": "You are an enterprise assistant.",
        "query": "What does the policy require?",
    }
    with TestClient(app) as client:
        first = client.post("/v1/context/process", json=payload)
        assert first.status_code == 200
        second = client.post("/v1/context/process", json=payload)
        assert second.status_code == 429
        assert int(second.headers["retry-after"]) >= 1
