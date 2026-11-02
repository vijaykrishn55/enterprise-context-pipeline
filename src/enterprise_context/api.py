"""FastAPI application exposing the context orchestration service."""
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .budget import TokenBudgetExceeded
from .config import Settings
from .observability import configure_logging, configure_tracing
from .errors import PipelineOverloaded
from .pipeline import EnterpriseContextPipeline
from .rate_limit import RateLimitExceeded
from .router import ProviderUnavailable
from .schemas import ContextPayload, PipelineResponse

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    chosen_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(chosen_settings.log_level)
        configure_tracing()
        pipeline = EnterpriseContextPipeline(chosen_settings)
        await pipeline.startup()
        app.state.pipeline = pipeline
        yield
        await pipeline.close()

    app = FastAPI(title="Enterprise Context Orchestration Service", version="1.0.0",
                  description="Async context compilation, caching, rate limiting, and resilient provider routing.",
                  lifespan=lifespan)

    @app.middleware("http")
    async def request_metadata(request: Request, call_next):
        supplied_id = request.headers.get("x-request-id", "")
        request_id = supplied_id if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", supplied_id) else uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled API error", extra={"request_id": request_id, "event": "api_error"})
            raise
        response.headers["x-request-id"] = request_id
        response.headers["x-process-time-ms"] = f"{(time.perf_counter()-started)*1000:.2f}"
        return response

    @app.get("/health")
    async def health(request: Request):
        return await request.app.state.pipeline.health()

    @app.get("/metrics")
    async def metrics(request: Request):
        return request.app.state.pipeline.metrics.snapshot()

    @app.post("/v1/context/process", response_model=PipelineResponse)
    async def process(payload: ContextPayload, request: Request, response: Response):
        try:
            result = await request.app.state.pipeline.process_request(payload, request_id=request.state.request_id)
            response.headers["X-Context-Cache"] = "HIT" if result.cache_hit else "MISS"
            response.headers["X-Cache-Backend"] = result.cache_backend
            response.headers["X-Provider"] = result.provider_used
            response.headers["X-Failover-Used"] = str(result.failover_used).lower()
            return result
        except TokenBudgetExceeded as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except RateLimitExceeded as exc:
            request.app.state.pipeline.metrics.increment("rate_limited_requests")
            logger.warning("Tenant rate limit exceeded", extra={"request_id": request.state.request_id,
                           "tenant_id": payload.tenant_id, "event": "rate_limited",
                           "retry_after": exc.retry_after})
            return JSONResponse(status_code=429, content={"detail": str(exc)},
                                headers={"Retry-After": str(exc.retry_after)})
        except PipelineOverloaded as exc:
            return JSONResponse(status_code=503, content={"detail": str(exc)},
                                headers={"Retry-After": "1"})
        except ProviderUnavailable as exc:
            raise HTTPException(status_code=503, detail="All configured LLM providers are unavailable.") from exc

    @app.get("/")
    async def root():
        return {"service": "enterprise-context-pipeline", "version": "1.0.0",
                "docs": "/docs", "health": "/health"}

    return app


app = create_app()
