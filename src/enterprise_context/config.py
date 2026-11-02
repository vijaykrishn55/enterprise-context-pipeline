"""Environment-backed settings with safe defaults for offline development."""
from dataclasses import dataclass
import os


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _float(name: str, default: float, minimum: float = 0.001) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    primary_provider: str = "mock"
    primary_model: str = "mock-primary"
    primary_base_url: str = ""
    primary_api_key: str = ""
    fallback_provider: str = "mock"
    fallback_model: str = "mock-fallback"
    fallback_base_url: str = ""
    fallback_api_key: str = ""
    redis_enabled: bool = True
    redis_cluster_enabled: bool = False
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_ttl: int = 300
    max_concurrent_requests: int = 50
    max_context_tokens: int = 32000
    provider_timeout_seconds: float = 15.0
    provider_max_retries: int = 2
    retry_base_seconds: float = 0.1
    tenant_rpm_limit: int = 60
    tenant_tpm_limit: int = 30000
    rate_limit_enabled: bool = True
    log_level: str = "INFO"

    @property
    def redis_url(self) -> str:
        configured = os.getenv("REDIS_URL", "").strip()
        if configured:
            return configured
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/0"
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @classmethod
    def from_env(cls) -> "Settings":
        primary_provider = os.getenv("PRIMARY_PROVIDER", "mock").lower()
        fallback_provider = os.getenv("FALLBACK_PROVIDER", "mock").lower()
        return cls(
            primary_provider=primary_provider,
            primary_model=os.getenv("PRIMARY_LLM_MODEL", "mock-primary"),
            primary_base_url=os.getenv(
                "PRIMARY_LLM_BASE_URL",
                "https://api.openai.com/v1" if primary_provider in {"openai", "openai-compatible"} else "",
            ).rstrip("/"),
            primary_api_key=os.getenv(
                "PRIMARY_LLM_API_KEY",
                os.getenv("OPENAI_API_KEY", "") if primary_provider in {"openai", "openai-compatible"} else "",
            ),
            fallback_provider=fallback_provider,
            fallback_model=os.getenv("FALLBACK_LLM_MODEL", "mock-fallback"),
            fallback_base_url=os.getenv(
                "FALLBACK_LLM_BASE_URL",
                "https://api.openai.com/v1" if fallback_provider in {"openai", "openai-compatible"} else "",
            ).rstrip("/"),
            fallback_api_key=os.getenv(
                "FALLBACK_LLM_API_KEY",
                os.getenv("OPENAI_API_KEY", "") if fallback_provider in {"openai", "openai-compatible"} else "",
            ),
            redis_enabled=_bool("REDIS_ENABLED", True),
            redis_cluster_enabled=_bool("REDIS_CLUSTER_ENABLED", False),
            redis_host=os.getenv("REDIS_HOST", "localhost"),
            redis_port=_int("REDIS_PORT", 6379),
            redis_password=os.getenv("REDIS_PASSWORD", ""),
            redis_ttl=_int("REDIS_TTL", 300),
            max_concurrent_requests=_int("MAX_CONCURRENT_REQUESTS", 50),
            max_context_tokens=_int("MAX_CONTEXT_TOKENS", 32000),
            provider_timeout_seconds=_float("PROVIDER_TIMEOUT_SECONDS", 15.0),
            provider_max_retries=_int("PROVIDER_MAX_RETRIES", 2, minimum=0),
            retry_base_seconds=_float("RETRY_BASE_SECONDS", 0.1),
            tenant_rpm_limit=_int("TENANT_RPM_LIMIT", 60),
            tenant_tpm_limit=_int("TENANT_TPM_LIMIT", 30000),
            rate_limit_enabled=_bool("RATE_LIMIT_ENABLED", True),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
