from __future__ import annotations

from app import otel


def test_otel_public_api_is_safe_without_an_exporter() -> None:
    configured = otel.configure_telemetry()

    assert configured in {True, False}
    tracer = otel.get_tracer("tests.otel")
    with tracer.start_as_current_span("test.span") as span:
        span.set_attribute("test.case", "safe")
        span.record_exception(RuntimeError("test-only exception"))
        metadata = otel.current_trace_metadata()
        if otel.OTEL_AVAILABLE:
            assert len(metadata["otel_trace_id"]) == 32
            assert len(metadata["otel_span_id"]) == 16


def test_current_trace_metadata_is_safe_without_active_span() -> None:
    metadata = otel.current_trace_metadata()

    assert set(metadata).issubset({"otel_trace_id", "otel_span_id"})
    assert all(isinstance(value, str) for value in metadata.values())
