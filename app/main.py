from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from structlog.contextvars import bind_contextvars

from .incidents import disable, enable, status
from .logging_config import configure_logging, get_logger
from .metrics import record_error, record_received, snapshot
from .middleware import CorrelationIdMiddleware
from .otel import (
    configure_telemetry,
    get_tracer,
    record_safe_exception,
    shutdown_telemetry,
    telemetry_enabled,
)
from .pii import hash_user_id, summarize_text
from .schemas import ChatRequest, ChatResponse
from .tracing import tracing_enabled
from scripts.dashboard import HTML_PATH, dashboard_summary

# Install the OTEL provider before Langfuse's @observe decorators are created so
# both systems can use the same provider without silently losing local spans.
configure_telemetry()

from .agent import LabAgent

configure_logging()
log = get_logger()
app = FastAPI(title="Day 13 Observability Lab")
app.add_middleware(CorrelationIdMiddleware)
agent = LabAgent()


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    error_type = type(exc).__name__
    record_error(error_type)
    
    correlation_id = getattr(request.state, "correlation_id", "UNKNOWN")
    
    log.error(
        "unhandled_error",
        service="api",
        error_type=error_type,
        correlation_id=correlation_id,
        payload={"detail": str(exc)},
    )
    
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal Server Error",
            "error_type": error_type,
            "correlation_id": correlation_id
        }
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    error_type = "HTTPException"
    if exc.status_code >= 500:
        record_error(f"HTTP_{exc.status_code}")
        
    correlation_id = getattr(request.state, "correlation_id", "UNKNOWN")
    
    log.error(
        "http_error",
        service="api",
        status_code=exc.status_code,
        error_type=error_type,
        correlation_id=correlation_id,
        payload={"detail": exc.detail},
    )
    
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "error_type": error_type,
            "correlation_id": correlation_id
        }
    )


@app.on_event("startup")
async def startup() -> None:
    otel_enabled = configure_telemetry()
    log.info(
        "app_started",
        service=os.getenv("APP_NAME", "day13-observability-lab"),
        env=os.getenv("APP_ENV", "dev"),
        payload={"tracing_enabled": tracing_enabled(), "otel_enabled": otel_enabled},
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    shutdown_telemetry()


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "tracing_enabled": tracing_enabled(),
        "otel_enabled": telemetry_enabled(),
        "incidents": status(),
    }


@app.get("/metrics")
async def metrics() -> dict:
    return snapshot()


@app.get("/dashboard", include_in_schema=False)
async def dashboard() -> FileResponse:
    """Serve the six-panel dashboard from the same Railway app URL."""

    return FileResponse(HTML_PATH, media_type="text/html")


@app.get("/api/data", include_in_schema=False)
async def dashboard_data() -> dict:
    """Provide the JSON data consumed by the dashboard page."""

    return dashboard_summary()


@app.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    bind_contextvars(
        user_id_hash=hash_user_id(body.user_id),
        session_id=body.session_id,
        feature=body.feature,
        model=agent.model,
        env=os.getenv("APP_ENV", "dev"),
    )

    tracer = get_tracer("day13.api")
    with tracer.start_as_current_span("http.request") as span:
        span.set_attribute("http.route", "/chat")
        span.set_attribute("app.correlation_id", request.state.correlation_id)
        span.set_attribute("app.feature", body.feature)
        log.info(
            "request_received",
            service="api",
            payload={"message_preview": summarize_text(body.message)},
        )
        record_received()
        try:
            result = agent.run(
                user_id=body.user_id,
                feature=body.feature,
                session_id=body.session_id,
                message=body.message,
                correlation_id=request.state.correlation_id,
            )
            span.set_attribute("app.latency_ms", result.latency_ms)
            span.set_attribute("app.quality_score", result.quality_score)
            log.info(
                "response_sent",
                service="api",
                latency_ms=result.latency_ms,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cost_usd=result.cost_usd,
                quality_score=result.quality_score,
                payload={"answer_preview": summarize_text(result.answer)},
            )
            return ChatResponse(
                answer=result.answer,
                correlation_id=request.state.correlation_id,
                latency_ms=result.latency_ms,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cost_usd=result.cost_usd,
                quality_score=result.quality_score,
            )
        except Exception as exc:  # pragma: no cover
            error_type = type(exc).__name__
            span.set_attribute("error.type", error_type)
            record_safe_exception(span, exc)
            record_error(error_type)
            log.error(
                "request_failed",
                service="api",
                error_type=error_type,
                payload={"detail": str(exc), "message_preview": summarize_text(body.message)},
            )
            raise HTTPException(status_code=500, detail=error_type) from exc


@app.post("/incidents/{name}/enable")
async def enable_incident(name: str) -> JSONResponse:
    try:
        enable(name)
        log.warning("incident_enabled", service="control", payload={"name": name})
        return JSONResponse({"ok": True, "incidents": status()})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/incidents/{name}/disable")
async def disable_incident(name: str) -> JSONResponse:
    try:
        disable(name)
        log.warning("incident_disabled", service="control", payload={"name": name})
        return JSONResponse({"ok": True, "incidents": status()})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
