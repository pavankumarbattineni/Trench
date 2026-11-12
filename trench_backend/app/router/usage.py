"""The signed-in user's own LLM usage and cost (Settings -> Usage & cost)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.database.session import get_db
from app.router.deps import get_current_user
from app.schemas.usage import QueryUsagePage, UsageSummaryResponse
from app.service.usage_service import UsageService

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/summary", response_model=UsageSummaryResponse)
async def usage_summary(
    days: int = Query(default=30, ge=1, le=365),
    tz: str | None = Query(default=None, max_length=64),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UsageSummaryResponse:
    """Totals for the last `days` days (and the period before, for trends),
    spend per day in the caller's time zone `tz` (IANA name, default UTC),
    spend per model, and how much prompt caching saved."""
    return UsageSummaryResponse(
        **await UsageService.summary(db, user_id=current_user.id, days=days, tz=tz)
    )


@router.get("/queries", response_model=QueryUsagePage)
async def usage_queries(
    days: int = Query(default=30, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QueryUsagePage:
    """One row per question asked in the last `days` days, newest first,
    with tokens, cost and the per-step breakdown."""
    return QueryUsagePage(
        **await UsageService.queries(
            db, user_id=current_user.id, days=days, page=page, page_size=page_size
        )
    )
