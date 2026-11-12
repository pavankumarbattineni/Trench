"""Stores per-call LLM usage and answers the Usage & cost page's queries.

Writing is best-effort by design: usage tracking must never be able to fail
or slow down a chat turn, so `record_calls` swallows (and logs) any error.
"""

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import LLMUsage, User
from app.database.session import async_session_factory
from app.service import llm_cost_service
from app.utils.usage_tracking import LLMCallUsage

logger = logging.getLogger(__name__)

_PREVIEW_LENGTH = 200
_ZERO = Decimal(0)


def _zone(tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


class UsageService:
    @staticmethod
    async def record_calls(
        *,
        user_id: uuid.UUID,
        thread_id: uuid.UUID | None,
        assistant_message_id: uuid.UUID | None,
        query: str,
        calls: list[LLMCallUsage],
    ) -> None:
        """Prices and stores one turn's LLM calls. Never raises."""
        if not calls:
            return
        try:
            async with async_session_factory() as db:
                user = await db.get(User, user_id)
                if user is None:
                    return
                preview = " ".join(query.split())[:_PREVIEW_LENGTH]
                # One question's calls share a turn id even with no answer
                # message (e.g. a turn that failed before creating one).
                turn_id = assistant_message_id or uuid.uuid4()
                for call in calls:
                    db.add(
                        LLMUsage(
                            user_id=user_id,
                            tenant_id=user.tenant_id,
                            thread_id=thread_id,
                            assistant_message_id=assistant_message_id,
                            turn_id=turn_id,
                            query_preview=preview,
                            step=call.step,
                            provider=call.provider_name,
                            model=call.model_name,
                            key_owner=call.key_owner,
                            input_tokens=call.input_tokens,
                            cached_input_tokens=call.cached_input_tokens,
                            output_tokens=call.output_tokens,
                            cost_usd=llm_cost_service.compute_cost(
                                provider_name=call.provider_name,
                                model_name=call.model_name,
                                input_tokens=call.input_tokens,
                                cached_input_tokens=call.cached_input_tokens,
                                output_tokens=call.output_tokens,
                            ),
                            estimated=call.estimated,
                        )
                    )
                await db.commit()
        except Exception:
            logger.exception(
                "Failed to record LLM usage | user_id=%s message_id=%s",
                user_id,
                assistant_message_id,
            )

    # --- Reads for the Usage & cost page --------------------------------

    @staticmethod
    async def summary(
        db: AsyncSession, *, user_id: uuid.UUID, days: int, tz: str | None
    ) -> dict:
        zone = _zone(tz)
        now = datetime.now(UTC)
        start = now - timedelta(days=days)
        previous_start = start - timedelta(days=days)

        async def totals(since: datetime, until: datetime) -> dict:
            row = (
                await db.execute(
                    select(
                        func.coalesce(func.sum(LLMUsage.cost_usd), _ZERO),
                        func.count(
                            distinct(
                                case(
                                    (
                                        LLMUsage.step == "generate",
                                        LLMUsage.turn_id,
                                    )
                                )
                            )
                        ),
                        func.coalesce(func.sum(LLMUsage.input_tokens), 0),
                        func.coalesce(func.sum(LLMUsage.cached_input_tokens), 0),
                        func.coalesce(func.sum(LLMUsage.output_tokens), 0),
                        func.count().filter(LLMUsage.cost_usd.is_(None)),
                        func.count().filter(LLMUsage.estimated.is_(True)),
                    ).where(
                        LLMUsage.user_id == user_id,
                        LLMUsage.created_at >= since,
                        LLMUsage.created_at < until,
                    )
                )
            ).one()
            return {
                "cost_usd": row[0],
                "questions": row[1],
                "input_tokens": row[2],
                "cached_input_tokens": row[3],
                "output_tokens": row[4],
                "unpriced_calls": row[5],
                "estimated_calls": row[6],
            }

        current = await totals(start, now)
        previous = await totals(previous_start, start)

        # Bucketed by the viewer's local day in Python, not SQL: Postgres
        # images don't all ship every zone alias browsers report (e.g.
        # "Asia/Calcutta"), and an unknown name there is a hard error.
        call_rows = (
            await db.execute(
                select(
                    LLMUsage.created_at,
                    LLMUsage.cost_usd,
                    LLMUsage.key_owner,
                    LLMUsage.step,
                    LLMUsage.turn_id,
                ).where(LLMUsage.user_id == user_id, LLMUsage.created_at >= start)
            )
        ).all()
        platform_by_day: dict = defaultdict(lambda: _ZERO)
        own_by_day: dict = defaultdict(lambda: _ZERO)
        questions_by_day: dict = defaultdict(set)
        for created_at, cost, key_owner, step, message_id in call_rows:
            day = created_at.astimezone(zone).date()
            if cost is not None:
                if key_owner == "platform":
                    platform_by_day[day] += cost
                else:
                    own_by_day[day] += cost
            if step == "generate" and message_id is not None:
                questions_by_day[day].add(message_id)
        today = now.astimezone(zone).date()
        daily = []
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            daily.append(
                {
                    "date": day.isoformat(),
                    "platform_cost_usd": platform_by_day.get(day, _ZERO),
                    "own_key_cost_usd": own_by_day.get(day, _ZERO),
                    "questions": len(questions_by_day.get(day, ())),
                }
            )

        model_rows = (
            await db.execute(
                select(
                    LLMUsage.provider,
                    LLMUsage.model,
                    func.count(
                        distinct(
                            case(
                                (
                                    LLMUsage.step == "generate",
                                    LLMUsage.turn_id,
                                )
                            )
                        )
                    ),
                    func.sum(LLMUsage.input_tokens),
                    func.sum(LLMUsage.cached_input_tokens),
                    func.sum(LLMUsage.output_tokens),
                    func.sum(LLMUsage.cost_usd),
                    func.count().filter(LLMUsage.cost_usd.is_(None)),
                )
                .where(LLMUsage.user_id == user_id, LLMUsage.created_at >= start)
                .group_by(LLMUsage.provider, LLMUsage.model)
                .order_by(func.sum(LLMUsage.cost_usd).desc().nulls_last())
            )
        ).all()
        by_model = []
        savings = _ZERO
        for provider, model, questions, inp, cached, out, cost, unpriced in model_rows:
            saved = llm_cost_service.cache_savings(
                provider_name=provider,
                model_name=model,
                cached_input_tokens=cached or 0,
            )
            savings += saved or _ZERO
            by_model.append(
                {
                    "provider": provider,
                    "model": model,
                    "questions": questions,
                    "input_tokens": inp or 0,
                    "cached_input_tokens": cached or 0,
                    "output_tokens": out or 0,
                    "cost_usd": cost,
                    "priced": unpriced == 0,
                }
            )

        return {
            "days": days,
            "current": current,
            "previous": previous,
            "cache_savings_usd": savings,
            "daily": daily,
            "by_model": by_model,
        }

    @staticmethod
    async def queries(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        days: int,
        page: int,
        page_size: int,
    ) -> dict:
        """One entry per question (all LLM calls for one answer), newest
        first, each with its per-step breakdown."""
        since = datetime.now(UTC) - timedelta(days=days)
        scope = (
            LLMUsage.user_id == user_id,
            LLMUsage.created_at >= since,
            LLMUsage.turn_id.is_not(None),
        )
        total = (
            await db.execute(
                select(func.count(distinct(LLMUsage.turn_id))).where(*scope)
            )
        ).scalar_one()
        message_rows = (
            await db.execute(
                select(LLMUsage.turn_id, func.min(LLMUsage.created_at))
                .where(*scope)
                .group_by(LLMUsage.turn_id)
                .order_by(func.min(LLMUsage.created_at).desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        ids = [row[0] for row in message_rows]
        calls_by_message: dict[uuid.UUID, list[LLMUsage]] = defaultdict(list)
        if ids:
            rows = (
                await db.execute(
                    select(LLMUsage)
                    .where(LLMUsage.turn_id.in_(ids))
                    .order_by(LLMUsage.created_at)
                )
            ).scalars()
            for row in rows:
                calls_by_message[row.turn_id].append(row)

        items = []
        for message_id, created_at in message_rows:
            calls = calls_by_message[message_id]
            answer = next((c for c in calls if c.step == "generate"), calls[0])
            costs = [c.cost_usd for c in calls]
            items.append(
                {
                    "assistant_message_id": message_id,
                    "thread_id": answer.thread_id,
                    "query_preview": answer.query_preview,
                    "created_at": created_at,
                    "provider": answer.provider,
                    "model": answer.model,
                    "key_owner": answer.key_owner,
                    "input_tokens": sum(c.input_tokens for c in calls),
                    "cached_input_tokens": sum(c.cached_input_tokens for c in calls),
                    "output_tokens": sum(c.output_tokens for c in calls),
                    "cost_usd": sum((c for c in costs if c is not None), _ZERO)
                    if any(c is not None for c in costs)
                    else None,
                    "fully_priced": all(c is not None for c in costs),
                    "estimated": any(c.estimated for c in calls),
                    "steps": [
                        {
                            "step": c.step,
                            "provider": c.provider,
                            "model": c.model,
                            "key_owner": c.key_owner,
                            "input_tokens": c.input_tokens,
                            "cached_input_tokens": c.cached_input_tokens,
                            "output_tokens": c.output_tokens,
                            "cost_usd": c.cost_usd,
                            "estimated": c.estimated,
                        }
                        for c in calls
                    ],
                }
            )
        return {"total": total, "page": page, "page_size": page_size, "items": items}
