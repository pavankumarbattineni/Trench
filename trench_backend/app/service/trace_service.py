"""Reads for Tracing. Every query is filtered by the caller's user id."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Date, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Trace, TraceSpan
from app.utils import langsmith_client


def _since(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _zone(tz: str | None) -> str:
    """A valid IANA zone name for per-day buckets, else UTC."""
    if not tz:
        return "UTC"
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC"
    return tz


class TraceService:
    @staticmethod
    async def list(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        days: int,
        status: str | None,
        kind: str | None,
        q: str | None,
        page: int,
        page_size: int,
    ) -> dict:
        filters = [Trace.user_id == user_id, Trace.start_time >= _since(days)]
        if status:
            filters.append(Trace.status == status)
        if kind:
            filters.append(Trace.kind == kind)
        if q and q.strip():
            like = f"%{q.strip()}%"
            filters.append(
                or_(
                    Trace.name.ilike(like),
                    Trace.input_preview.ilike(like),
                    Trace.output_preview.ilike(like),
                )
            )
        total = (
            await db.execute(select(func.count()).select_from(Trace).where(*filters))
        ).scalar_one()
        rows = (
            (
                await db.execute(
                    select(Trace)
                    .where(*filters)
                    .order_by(Trace.start_time.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        last_synced = (
            await db.execute(
                select(func.max(Trace.synced_at)).where(Trace.user_id == user_id)
            )
        ).scalar_one_or_none()
        return {
            "items": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "enabled": langsmith_client.tracing_enabled(),
            "last_synced_at": last_synced,
        }

    @staticmethod
    async def stats(
        db: AsyncSession, *, user_id: uuid.UUID, days: int, tz: str | None
    ) -> dict:
        """Volume, errors, latency percentiles, tokens and cost; per day (in
        the caller's time zone) and per model."""
        where = (Trace.user_id == user_id, Trace.start_time >= _since(days))
        p50 = func.percentile_cont(0.5).within_group(Trace.latency_ms)
        p95 = func.percentile_cont(0.95).within_group(Trace.latency_ms)
        errors = func.count().filter(Trace.status == "error")
        tokens = func.coalesce(func.sum(Trace.total_tokens), 0)
        cost = func.coalesce(func.sum(Trace.total_cost), 0)

        totals = (
            await db.execute(
                select(func.count(), errors, p50, p95, tokens, cost).where(*where)
            )
        ).one()
        day = cast(func.timezone(_zone(tz), Trace.start_time), Date)
        by_day = (
            await db.execute(
                select(day, func.count(), errors, p50, tokens, cost)
                .where(*where)
                .group_by(day)
                .order_by(day)
            )
        ).all()
        by_model = (
            await db.execute(
                select(
                    TraceSpan.model,
                    func.count(),
                    func.coalesce(func.sum(TraceSpan.total_tokens), 0),
                    func.coalesce(func.sum(TraceSpan.total_cost), 0),
                    func.avg(TraceSpan.latency_ms),
                )
                .join(Trace, Trace.id == TraceSpan.trace_id)
                .where(
                    *where, TraceSpan.run_type == "llm", TraceSpan.model.is_not(None)
                )
                .group_by(TraceSpan.model)
                .order_by(func.count().desc())
            )
        ).all()

        count, failed = totals[0], totals[1]

        def ms(value) -> int | None:
            return int(value) if value is not None else None

        return {
            "days": days,
            "traces": count,
            "errors": failed,
            "error_rate": round(failed / count, 4) if count else 0.0,
            "p50_latency_ms": ms(totals[2]),
            "p95_latency_ms": ms(totals[3]),
            "total_tokens": int(totals[4]),
            "total_cost": float(totals[5]),
            "by_day": [
                {
                    "date": d.isoformat(),
                    "traces": c,
                    "errors": e,
                    "p50_latency_ms": ms(p),
                    "total_tokens": int(t),
                    "total_cost": float(x),
                }
                for d, c, e, p, t, x in by_day
            ],
            "by_model": [
                {
                    "model": m,
                    "calls": c,
                    "total_tokens": int(t),
                    "total_cost": float(x),
                    "avg_latency_ms": ms(lat),
                }
                for m, c, t, x, lat in by_model
            ],
        }

    @staticmethod
    async def get(db: AsyncSession, *, user_id: uuid.UUID, trace_id: uuid.UUID):
        """(trace, spans) or None when it isn't the caller's."""
        trace = (
            await db.execute(
                select(Trace).where(Trace.id == trace_id, Trace.user_id == user_id)
            )
        ).scalar_one_or_none()
        if trace is None:
            return None
        spans = (
            (
                await db.execute(
                    select(TraceSpan)
                    .where(TraceSpan.trace_id == trace_id)
                    .order_by(TraceSpan.position)
                )
            )
            .scalars()
            .all()
        )
        return trace, spans
