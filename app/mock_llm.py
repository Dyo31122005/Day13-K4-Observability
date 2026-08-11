from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .incidents import STATE
from .otel import get_tracer, record_safe_exception


@dataclass
class FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class FakeResponse:
    text: str
    usage: FakeUsage
    model: str


class FakeLLM:
    def __init__(self, model: str = "claude-sonnet-4-5") -> None:
        self.model = model

    def generate(self, prompt: str) -> FakeResponse:
        tracer = get_tracer("day13.llm")
        with tracer.start_as_current_span("llm.generate") as span:
            span.set_attribute("llm.model", self.model)
            span.set_attribute("llm.prompt_length", len(prompt))
            span.set_attribute("incident.cost_spike", bool(STATE["cost_spike"]))
            try:
                time.sleep(0.15)
                input_tokens = max(20, len(prompt) // 4)
                output_tokens = random.randint(80, 180)
                if STATE["cost_spike"]:
                    output_tokens *= 4
                answer = (
                    "Starter answer. Teams should improve this output logic and add better quality checks. "
                    "Use retrieved context and keep responses concise."
                )
                span.set_attribute("llm.input_tokens", input_tokens)
                span.set_attribute("llm.output_tokens", output_tokens)
                return FakeResponse(
                    text=answer,
                    usage=FakeUsage(input_tokens, output_tokens),
                    model=self.model,
                )
            except Exception as exc:
                span.set_attribute("error.type", type(exc).__name__)
                record_safe_exception(span, exc)
                raise
