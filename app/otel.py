from __future__ import annotations

import os
from typing import Any

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised when optional OTEL packages are absent
    trace = None  # type: ignore[assignment]
    Resource = None  # type: ignore[assignment,misc]
    TracerProvider = None  # type: ignore[assignment,misc]
    ConsoleSpanExporter = None  # type: ignore[assignment,misc]
    SimpleSpanProcessor = None  # type: ignore[assignment,misc]
    OTEL_AVAILABLE = False

try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
except ImportError:  # pragma: no cover - OTLP is optional when console export is enough
    OTLPSpanExporter = None  # type: ignore[assignment,misc]


class _NoopSpan:
    def __enter__(self) -> _NoopSpan:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        return False

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def record_exception(self, exception: BaseException, attributes: dict[str, Any] | None = None) -> None:
        return None

    def add_event(self, name: str, attributes: dict[str, Any] | None = None, timestamp: int | None = None) -> None:
        return None


class _NoopTracer:
    def start_as_current_span(self, name: str, **kwargs: Any) -> _NoopSpan:
        return _NoopSpan()


_configured = False
_owned_provider: Any | None = None


def configure_telemetry() -> bool:
    """Configure a local OTEL provider without making OTEL a hard runtime dependency."""

    global _configured, _owned_provider
    if _configured:
        return OTEL_AVAILABLE
    _configured = True

    if not OTEL_AVAILABLE:
        return False

    existing_provider = trace.get_tracer_provider()
    if type(existing_provider).__name__ != "ProxyTracerProvider":
        # Langfuse or an embedding application may already own the provider.
        return True

    resource = Resource.create(
        {
            "service.name": os.getenv(
                "OTEL_SERVICE_NAME", os.getenv("APP_NAME", "day13-observability-lab")
            ),
            "deployment.environment": os.getenv("APP_ENV", "dev"),
        }
    )
    provider = TracerProvider(resource=resource)
    exporter_mode = os.getenv("OTEL_TRACES_EXPORTER", "console").lower()
    if exporter_mode == "console":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    elif exporter_mode == "otlp" and OTLPSpanExporter is not None:
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or None
        exporter = OTLPSpanExporter(endpoint=endpoint) if endpoint else OTLPSpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif exporter_mode not in {"none", ""}:
        # Keep local runs observable without silently requiring an OTLP collector.
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _owned_provider = provider
    return True


def get_tracer(name: str):
    if not OTEL_AVAILABLE:
        return _NoopTracer()
    return trace.get_tracer(name)


def telemetry_enabled() -> bool:
    return OTEL_AVAILABLE


def current_trace_metadata() -> dict[str, str]:
    """Return safe IDs for linking an OTEL span to Langfuse metadata."""

    if not OTEL_AVAILABLE:
        return {}
    span = trace.get_current_span()
    context = span.get_span_context()
    if not context.is_valid:
        return {}
    return {
        "otel_trace_id": format(context.trace_id, "032x"),
        "otel_span_id": format(context.span_id, "016x"),
    }


def record_safe_exception(span: Any, exception: BaseException) -> None:
    """Record only the exception type so exception messages cannot leak PII."""

    span.add_event(
        "exception",
        attributes={"exception.type": type(exception).__name__},
    )


def shutdown_telemetry() -> None:
    """Flush owned spans without tearing down the process-wide provider."""

    if _owned_provider is not None:
        _owned_provider.force_flush()
