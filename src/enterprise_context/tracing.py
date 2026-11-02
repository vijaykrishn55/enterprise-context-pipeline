"""Optional OpenTelemetry spans with a graceful no-op fallback."""
from functools import wraps

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
except ImportError:  # Keeps source-level tests runnable before dependency installation.
    trace = None
    Status = StatusCode = None


def traced(span_name: str):
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            if trace is None:
                return await function(*args, **kwargs)
            tracer = trace.get_tracer("enterprise_context")
            with tracer.start_as_current_span(span_name) as span:
                try:
                    return await function(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise
        return wrapped
    return decorate
