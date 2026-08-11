from __future__ import annotations

import time

from .incidents import STATE
from .otel import get_tracer, record_safe_exception

CORPUS = {
    "refund": ["Refunds are available within 7 days with proof of purchase."],
    "monitoring": ["Metrics detect incidents, traces localize them, logs explain root cause."],
    "policy": ["Do not expose PII in logs. Use sanitized summaries only."],
}


def retrieve(message: str) -> list[str]:
    tracer = get_tracer("day13.rag")
    with tracer.start_as_current_span("rag.retrieve") as span:
        span.set_attribute("incident.rag_slow", bool(STATE["rag_slow"]))
        span.set_attribute("incident.tool_fail", bool(STATE["tool_fail"]))
        try:
            if STATE["tool_fail"]:
                raise RuntimeError("Vector store timeout")
            if STATE["rag_slow"]:
                time.sleep(2.5)
            lowered = message.lower()
            for key, docs in CORPUS.items():
                if key in lowered:
                    span.set_attribute("rag.document_count", len(docs))
                    return docs
            fallback = ["No domain document matched. Use general fallback answer."]
            span.set_attribute("rag.document_count", len(fallback))
            return fallback
        except Exception as exc:
            span.set_attribute("error.type", type(exc).__name__)
            record_safe_exception(span, exc)
            raise
