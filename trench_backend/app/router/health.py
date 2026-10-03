"""Health-check endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.schemas.health import HealthResponse
from app.service.health_service import HealthService

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """Reports whether the API and its database connection are healthy.

    Args:
        db: An active async SQLAlchemy session.

    Returns:
        HealthResponse with status "ok" and the database connectivity state.
    """
    db_status = await HealthService.check_database(db)
    return HealthResponse(status="ok", db=db_status)
