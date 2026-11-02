"""Run two identical requests to demonstrate cache miss followed by cache hit."""
import asyncio
import json

from enterprise_context.pipeline import EnterpriseContextPipeline
from enterprise_context.schemas import ContextPayload


async def main() -> None:
    pipeline = EnterpriseContextPipeline()
    await pipeline.startup()
    payload = ContextPayload(
        tenant_id="demo-tenant", user_id="demo-user", session_id="demo-session",
        system_prompt="You are a compliance assistant. Preserve all system instructions.",
        static_context=["Policy 4.2: approved encryption standard is AES-256."],
        dynamic_context=["The security handbook requires AES-256 encryption."],
        query="What encryption standard is required?",
    )
    for index in (1, 2):
        result = await pipeline.process_request(payload)
        print(f"Request {index}:\n{json.dumps(result.model_dump(), indent=2)}")
    print("Metrics:", json.dumps(pipeline.metrics.snapshot(), indent=2))
    await pipeline.close()


if __name__ == "__main__":
    asyncio.run(main())
