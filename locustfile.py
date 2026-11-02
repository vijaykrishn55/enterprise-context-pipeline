"""Locust scenario using an identical full request to exercise exact-response caching.
Run: locust -f locustfile.py --headless -u 50 -r 10 -t 1m --host http://127.0.0.1:8000
Set tenant RPM/TPM limits high enough for the intended test before launching.
"""
from locust import HttpUser, between, task


class ContextPipelineUser(HttpUser):
    wait_time = between(0.0, 0.05)
    payload = {
        "tenant_id": "load-test-tenant",
        "user_id": "load-test-user",
        "session_id": "static-session",
        "system_prompt": "You are an enterprise compliance assistant. Preserve all instructions.",
        "static_context": ["Policy 4.2 requires AES-256 encryption.", "Audit logs are retained for 365 days."],
        "dynamic_context": ["The internal security handbook names AES-256 as the approved standard."],
        "history": [],
        "query": "Which encryption standard is required by Policy 4.2?",
        "temperature": 0.0,
    }

    @task
    def process_repeated_context(self):
        with self.client.post("/v1/context/process", json=self.payload,
                              name="/v1/context/process (repeat cache key)", catch_response=True) as response:
            cache_header = response.headers.get("X-Context-Cache")
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}: {response.text[:150]}")
            elif cache_header not in {"HIT", "MISS"}:
                response.failure("Missing X-Context-Cache header")
            else:
                response.success()
