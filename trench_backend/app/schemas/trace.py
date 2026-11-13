"""Tracing: the signed-in user's own LangSmith traces."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class TraceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    knowledge_type: str | None = None
    name: str
    status: str
    error: str | None = None
    thread_id: uuid.UUID | None = None
    start_time: datetime
    end_time: datetime | None = None
    latency_ms: int | None = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost: float | None = None
    span_count: int
    llm_calls: int
    tool_calls: int
    retriever_calls: int
    error_count: int
    models: list[str]
    input_preview: str | None = None
    output_preview: str | None = None
    complete: bool


class TraceListResponse(BaseModel):
    items: list[TraceSummary]
    total: int
    page: int
    page_size: int
    # False when LangSmith tracing isn't configured for this deployment.
    enabled: bool
    last_synced_at: datetime | None = None


class TraceSpanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parent_id: uuid.UUID | None = None
    position: int
    depth: int
    name: str
    run_type: str
    status: str
    error: str | None = None
    start_time: datetime
    end_time: datetime | None = None
    latency_ms: int | None = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost: float | None = None
    model: str | None = None
    inputs: Any | None = None
    outputs: Any | None = None
    inputs_truncated: bool
    outputs_truncated: bool
    meta: dict | None = None


class TraceDetail(TraceSummary):
    spans: list[TraceSpanOut]


class TraceDayStat(BaseModel):
    date: str
    traces: int
    errors: int
    p50_latency_ms: int | None = None
    total_tokens: int
    total_cost: float


class TraceModelStat(BaseModel):
    model: str
    calls: int
    total_tokens: int
    total_cost: float
    avg_latency_ms: int | None = None


class TraceStats(BaseModel):
    days: int
    traces: int
    errors: int
    error_rate: float
    p50_latency_ms: int | None = None
    p95_latency_ms: int | None = None
    total_tokens: int
    total_cost: float
    by_day: list[TraceDayStat]
    by_model: list[TraceModelStat]


class TraceSyncResponse(BaseModel):
    enabled: bool
    synced: int
