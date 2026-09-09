"""Business logic for the health-check endpoint."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class HealthService:
    """Checks the health of Trench's dependencies (currently: the database)."""

    @staticmethod
    async def check_database(db: AsyncSession) -> str:
        """Runs a trivial query to confirm the database connection is alive.

        Args:
            db: An active async SQLAlchemy session.

        Returns:
            "connected" if the query succeeds.
        """
        await db.execute(text("SELECT 1"))
        return "connected"
