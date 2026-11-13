"""Tracing: the signed-in user's own LangSmith traces, as copied by
app/service/trace_sync_service.py."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.database.session import get_db
from app.router.deps import get_current_user
from app.schemas.trace import (
    TraceDetail,
    TraceListResponse,
    TraceSpanOut,
    TraceStats,
    TraceSummary,
    TraceSyncResponse,
)
from app.service import trace_sync_service
from app.service.trace_service import TraceService

router = APIRouter(prefix="/traces", tags=["traces"])

_MAX_DAYS = trace_sync_service.RETENTION_DAYS


@router.get("", response_model=TraceListResponse)
async def list_traces(
    days: int = Query(default=7, ge=1, le=_MAX_DAYS),
    status_filter: Literal["success", "error", "pending"] | None = Query(
        default=None, alias="status"
    ),
    kind: Literal["chat_turn", "thread_title"] | None = None,
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TraceListResponse:
    """The caller's traces, newest first."""
    result = await TraceService.list(
        db,
        user_id=current_user.id,
        days=days,
        status=status_filter,
        kind=kind,
        q=q,
        page=page,
        page_size=page_size,
    )
    result["items"] = [TraceSummary.model_validate(t) for t in result["items"]]
    return TraceListResponse(**result)


@router.get("/stats", response_model=TraceStats)
async def trace_stats(
    days: int = Query(default=7, ge=1, le=_MAX_DAYS),
    tz: str | None = Query(default=None, max_length=64),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TraceStats:
    """Volume, errors, latency percentiles, tokens and cost for the period,
    per day (in the caller's time zone `tz`) and per model."""
    return TraceStats(
        **await TraceService.stats(db, user_id=current_user.id, days=days, tz=tz)
    )


@router.post("/sync", response_model=TraceSyncResponse)
async def sync_traces(
    current_user: User = Depends(get_current_user),
) -> TraceSyncResponse:
    """Pull the caller's recent traces from LangSmith that are missing or were
    incomplete (the automatic sync after each run covers the rest)."""
    try:
        result = await trace_sync_service.sync_recent(current_user.id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LangSmith is unreachable right now. Try again shortly.",
        ) from exc
    return TraceSyncResponse(**result)


@router.get("/{trace_id}", response_model=TraceDetail)
async def get_trace(
    trace_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TraceDetail:
    found = await TraceService.get(db, user_id=current_user.id, trace_id=trace_id)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trace not found"
        )
    trace, spans = found
    return TraceDetail(
        **TraceSummary.model_validate(trace).model_dump(),
        spans=[TraceSpanOut.model_validate(s) for s in spans],
    )
