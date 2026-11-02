"""Validated request and response contracts for the orchestration service."""
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContextPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tenant_id: str = Field(default="default", min_length=1, max_length=128)
    user_id: str = Field(..., min_length=1, max_length=128)
    session_id: str = Field(..., min_length=1, max_length=128)
    system_prompt: str = Field(..., min_length=1, max_length=30000)
    static_context: list[str] = Field(default_factory=list, max_length=100)
    dynamic_context: list[str] = Field(default_factory=list, max_length=500)
    history: list[str] = Field(default_factory=list, max_length=200)
    query: str = Field(..., min_length=1, max_length=12000)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_context_tokens: int | None = Field(default=None, ge=128, le=200000)

    @field_validator("static_context", "dynamic_context", "history")
    @classmethod
    def validate_text_blocks(cls, values: list[str]) -> list[str]:
        if any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError("Context blocks must be non-empty strings.")
        if any(len(item) > 50000 for item in values):
            raise ValueError("Individual context blocks must be 50,000 characters or less.")
        return [item.strip() for item in values]


class PipelineResponse(BaseModel):
    request_id: str
    response_text: str
    cache_hit: bool
    latency_ms: float
    estimated_input_tokens: int
    processed_input_tokens: int
    output_tokens: int
    provider_cached_input_tokens: int = 0
    model_used: str
    provider_used: str
    failover_used: bool = False
    pruned_dynamic_chunks: int = 0
    dropped_history_messages: int = 0
    cache_backend: str = "memory"
