"""Collects LLM token usage for the duration of one chat turn.

A turn opens a collector (`collect_usage()`); every LLM call made while it is
open -- inside LangGraph nodes, which run in tasks that inherit this
context -- appends one `LLMCallUsage`. Nothing is recorded when no collector
is open, so callers that don't care (tests, scripts) are unaffected.

The *step* (condense / reformulate / generate / title) is set by the code
making the call via `usage_step(...)`, keeping LLMClientService.stream_generate's
signature unchanged.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class LLMCallUsage:
    step: str
    provider_name: str
    model_name: str
    key_owner: str  # "platform" | "personal" | "tenant"
    input_tokens: int  # total prompt tokens, cached ones included
    cached_input_tokens: int
    output_tokens: int
    # True when the provider never reported usage (e.g. the stream was
    # stopped mid-answer) and the counts are a tokenizer estimate.
    estimated: bool = False


_collector: ContextVar[list[LLMCallUsage] | None] = ContextVar(
    "llm_usage_collector", default=None
)
_step: ContextVar[str] = ContextVar("llm_usage_step", default="generate")


@contextmanager
def collect_usage() -> Iterator[list[LLMCallUsage]]:
    calls: list[LLMCallUsage] = []
    token = _collector.set(calls)
    try:
        yield calls
    finally:
        _collector.reset(token)


@contextmanager
def usage_step(step: str) -> Iterator[None]:
    token = _step.set(step)
    try:
        yield
    finally:
        _step.reset(token)


def current_step() -> str:
    return _step.get()


def record(usage: LLMCallUsage) -> None:
    calls = _collector.get()
    if calls is not None:
        calls.append(usage)
