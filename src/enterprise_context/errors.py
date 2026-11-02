"""Service-level errors translated into stable HTTP responses."""


class PipelineOverloaded(RuntimeError):
    """Raised when the configured in-flight request limit is saturated."""
