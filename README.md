# Enterprise Context Orchestration Pipeline

A modular, asynchronous FastAPI service for bounded enterprise context assembly, TTL caching, tenant rate limiting, and resilient primary/fallback LLM routing. It runs with a deterministic mock provider by default, so the full test suite and demo do not need a paid API key.

## What is implemented

- **Validated API contract:** Pydantic 2 request and response schemas, input bounds, and rejection of unexpected fields.
- **Context compiler and prefix alignment:** System instructions and static context are kept at the front of the compiled prompt. Dynamic context is added before history; the newest history is retained first when trimming is required.
- **Token budget enforcement:** A configurable context limit with a portable character-based token estimate; when RPM/TPM throttling is enabled, the effective limit is capped by the configured TPM bucket capacity. System instructions and the query are mandatory; a request is rejected when those anchors cannot fit. Pruning is reflected in response metadata and logs.
- **TTL caching:** Deterministic SHA-256 keys include tenant, user, session, provider/model, temperature, and compiled context to avoid cross-tenant or cross-session cache leakage. Redis is used when reachable; in-memory TTL cache is the development/failure fallback.
- **Cache stampede protection:** Requests with the same cache key share a local per-key lock and re-check the cache before dispatching upstream. This avoids duplicate concurrent upstream calls inside one process.
- **Tenant-aware throttling:** Atomic Redis Lua token buckets for RPM and TPM, with process-local fallback when Redis is unavailable. API rate-limit responses include HTTP 429 and `Retry-After`.
- **Resilient provider routing:** Async OpenAI-compatible HTTP adapter, bounded retries for transient failures, timeout handling, a three-failure circuit breaker, a half-open recovery probe, and fallback routing.
- **Concurrency and observability:** An async semaphore caps in-flight upstream work; JSON logs include request/tenant/provider events; OpenTelemetry spans instrument requests and routing, with optional OTLP HTTP export; `/health` and `/metrics` expose basic runtime state.
- **Deployment examples:** Dockerfile, Docker Compose with Redis 7.2, and Kubernetes Deployment/Service manifests.

#### Architecture

```text
Client
  |
  v
FastAPI + Pydantic validation + request ID
  |
  v
Context compiler / token budget / static-prefix alignment
  |
  +--> tenant RPM bucket (request admission)
  |
  v
TTL response cache + per-key request coalescing
  | cache hit
  +------------------------------> structured response + cache headers
  |
  | cache miss
  v
tenant TPM bucket -> concurrency semaphore -> primary provider
                                          | success
                                          +------> cache response + telemetry
                                          |
                                          | failure / circuit open
                                          v
                                      fallback provider
```

## Run on Windows PowerShell

Use Python 3.11 or newer.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,telemetry]"
Copy-Item .env.example .env
python -m pytest
python scripts/demo.py
```

The expected test result is all tests passing. The demo runs the same request twice; the first request should show a cache miss and the second should show a cache hit.

### Start the API without Docker

```powershell
uvicorn enterprise_context.api:app --app-dir src --env-file .env --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs`. Useful endpoints:

- `POST /v1/context/process` : compile and process a context request
- `GET /health` : basic readiness and router state
- `GET /metrics` : process-local counters and latency summary

The process adds `X-Request-ID`, `X-Process-Time-Ms`, `X-Context-Cache`, `X-Cache-Backend`, `X-Provider`, and `X-Failover-Used` headers where applicable. Input validation errors return 422, mandatory-content budget errors return 413, tenant rate limits return 429 with `Retry-After`, and complete provider unavailability returns 503.

### Example request

```json
{
  "tenant_id": "org-1",
  "user_id": "user-42",
  "session_id": "session-7",
  "system_prompt": "You are a careful compliance assistant. Preserve these instructions.",
  "static_context": ["Policy 4.2 requires AES-256 encryption."],
  "dynamic_context": ["The security handbook lists AES-256 as approved."],
  "history": [],
  "query": "Which encryption standard is required?",
  "temperature": 0.0
}
```

### Run with Docker Compose and Redis

Create `.env` as shown above, then run:

```powershell
docker compose up --build
```

Compose starts Redis and the API. The API waits for Redis health before starting; if Redis later becomes unavailable, caching and limiting fall back to process-local behavior and log a warning. In Kubernetes production, use a managed/high-availability Redis deployment rather than relying on local fallback for globally consistent quotas.

## Optional live provider configuration

The default provider is `mock`. To use an OpenAI-compatible endpoint, set the following in `.env` and restart the API:

```dotenv
PRIMARY_PROVIDER=openai-compatible
PRIMARY_LLM_MODEL=gpt-4o-mini
PRIMARY_LLM_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=replace-with-your-key
FALLBACK_PROVIDER=mock
FALLBACK_LLM_MODEL=mock-fallback
```

`PRIMARY_LLM_API_KEY` can be used instead of `OPENAI_API_KEY`. For Groq, set `PRIMARY_PROVIDER=groq`, `PRIMARY_LLM_BASE_URL=https://api.groq.com/openai/v1`, `PRIMARY_LLM_MODEL` to a model available to your account, and `PRIMARY_LLM_API_KEY` to your Groq key. Do not commit `.env` or API keys. The mock default is intentional so automated tests are deterministic and offline.

To verify failover locally with the mock provider, set `SIMULATE_FAILURE=true`; the simulated primary returns HTTP-503-style errors and the fallback mock should handle the request. To observe the circuit breaker open after three failures, send three requests with different queries or session IDs; repeating the exact same request is served from the response cache after the first fallback. Remove the variable or set it to `false` afterward.

## Verification

```powershell
python -m pytest
python scripts/demo.py
```

For exactly 1,000 repeated requests, use the fixed-count async load-test script. Raise the tenant's RPM capacity for the test duration and keep the endpoint local with synthetic data:

```powershell
# For the test only, set in .env and restart the API:
# TENANT_RPM_LIMIT=1200
# TENANT_TPM_LIMIT=100000
# MAX_CONCURRENT_REQUESTS=100
uvicorn enterprise_context.api:app --app-dir src --env-file .env --host 127.0.0.1 --port 8000
```

In another terminal: `python scripts/load_test.py --requests 1000 --concurrency 50`. It prints status counts, local cache-hit rate, throughput, and p50/p95/p99 latency. An alternative Locust scenario is available with `locust -f locustfile.py --headless -u 50 -r 10 -t 1m --host http://127.0.0.1:8000`.

Both scenarios send the same full request to exercise exact-response caching. Subsequent successful requests should report `X-Context-Cache: HIT`. In a report, distinguish this local cache from provider-native prompt/KV caching.

## Environment variables

| Variable                                           | Purpose                                                         | Default                                    |
| -------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------ |
| `PRIMARY_PROVIDER`                               | `mock`, `openai-compatible`, or `groq`                    | `mock`                                   |
| `PRIMARY_LLM_MODEL`                              | Primary model identifier                                        | `mock-primary`                           |
| `PRIMARY_LLM_BASE_URL`                           | OpenAI-compatible API base URL                                  | empty unless provider is OpenAI-compatible |
| `PRIMARY_LLM_API_KEY` / `OPENAI_API_KEY`       | Provider credential                                             | unset                                      |
| `FALLBACK_PROVIDER`                              | Fallback provider                                               | `mock`                                   |
| `FALLBACK_LLM_MODEL`                             | Fallback model identifier                                       | `mock-fallback`                          |
| `REDIS_ENABLED`                                  | Enable Redis preference                                         | `true`                                   |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` | Redis connection settings                                       | `localhost`, `6379`, empty             |
| `REDIS_CLUSTER_ENABLED`                          | Use redis-py Cluster client and cross-slot-safe rate-limit keys | `false`                                  |
| `REDIS_URL`                                      | Optional complete Redis URL                                     | unset                                      |
| `REDIS_TTL`                                      | Cache TTL in seconds                                            | `300`                                    |
| `MAX_CONCURRENT_REQUESTS`                        | Maximum concurrent upstream requests                            | `50`                                     |
| `MAX_CONTEXT_TOKENS`                             | Default context budget                                          | `32000`                                  |
| `PROVIDER_TIMEOUT_SECONDS`                       | Upstream timeout                                                | `15`                                     |
| `PROVIDER_MAX_RETRIES`                           | Retries after the first attempt                                 | `2`                                      |
| `TENANT_RPM_LIMIT`                               | Per-tenant request-bucket capacity                              | `60`                                     |
| `TENANT_TPM_LIMIT`                               | Per-tenant token-bucket capacity                                | `30000`                                  |
| `RATE_LIMIT_ENABLED`                             | Enable tenant quota checks                                      | `true`                                   |
| `OTEL_EXPORTER_OTLP_ENDPOINT`                    | Optional OTLP HTTP collector endpoint                           | unset                                      |

## Honest scope and known limitations

- The provider adapter currently supports the OpenAI Chat Completions-compatible request/response format and captures cached-token usage from common usage fields (such as `prompt_tokens_details.cached_tokens`) if the provider reports it. Provider-native cache controls and Anthropic-specific cache blocks need a dedicated adapter. The Redis TTL cache here stores **final responses**, not provider KV states.
- Token counts use a rough character-based estimator unless a provider reports usage. Production deployments should plug in a tokenizer matched to the selected model.
- Redis-backed RPM/TPM state is atomic across replicas, but local fallback state is only process-wide and cannot enforce a global quota while Redis is down.
- Metrics are process-local snapshots, not a replacement for Prometheus metrics or a durable event pipeline. OpenTelemetry spans are exported only when a collector endpoint and the telemetry extra are configured. The Kubernetes manifest is a deployment example, not a complete highly available cluster configuration; `REDIS_HOST` must point at a reachable Redis Cluster endpoint when `REDIS_CLUSTER_ENABLED=true`.
- The stated 15 ms orchestration overhead, 500 RPS / 99.99% availability targets, and 80% provider-cache-hit target require environment-specific load tests, provider-side measurements, and operational deployment evidence. They are not asserted merely because unit tests pass.
