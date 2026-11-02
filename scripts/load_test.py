"""Send a fixed number of identical requests and report cache/latency statistics."""
import argparse
import asyncio
import json
import time
from collections import Counter

import httpx

PAYLOAD = {
    "tenant_id": "load-test-tenant", "user_id": "load-test-user",
    "session_id": "static-session",
    "system_prompt": "You are an enterprise compliance assistant. Preserve all instructions.",
    "static_context": ["Policy 4.2 requires AES-256 encryption.",
                       "Audit logs are retained for 365 days."],
    "dynamic_context": ["The internal security handbook names AES-256 as the approved standard."],
    "history": [], "query": "Which encryption standard is required by Policy 4.2?",
    "temperature": 0.0,
}


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile_value))
    return round(ordered[index], 2)


async def run(base_url: str, count: int, concurrency: int, timeout: float) -> dict:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    statuses: Counter[str] = Counter()
    caches: Counter[str] = Counter()
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout) as client:
        async def send_one() -> None:
            async with semaphore:
                request_started = time.perf_counter()
                try:
                    response = await client.post(f"{base_url.rstrip('/')}/v1/context/process", json=PAYLOAD)
                    statuses[str(response.status_code)] += 1
                    if response.status_code == 200:
                        caches[response.headers.get("X-Context-Cache", "UNKNOWN")] += 1
                except httpx.HTTPError:
                    statuses["transport_error"] += 1
                latencies.append((time.perf_counter() - request_started) * 1000)
        await asyncio.gather(*(send_one() for _ in range(count)))
    duration = time.perf_counter() - started
    successes = statuses.get("200", 0)
    cache_hit = caches.get("HIT", 0)
    return {
        "requested": count, "concurrency": concurrency, "duration_seconds": round(duration, 3),
        "throughput_requests_per_second": round(count / duration, 2) if duration else 0,
        "status_counts": dict(statuses), "successful_requests": successes,
        "cache_hits": cache_hit, "cache_misses": caches.get("MISS", 0),
        "cache_hit_rate_successful": round(cache_hit / successes, 4) if successes else 0,
        "latency_ms": {"p50": percentile(latencies, 0.50), "p95": percentile(latencies, 0.95),
                       "p99": percentile(latencies, 0.99), "max": round(max(latencies), 2) if latencies else 0},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("--requests and --concurrency must be positive integers")
    result = asyncio.run(run(args.url, args.requests, args.concurrency, args.timeout))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
