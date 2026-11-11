"""Business logic for the health-check endpoint."""

import os

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

    @staticmethod
    def running_version() -> str:
        """The git commit SHA this instance was deployed from.

        Render injects RENDER_GIT_COMMIT into every deploy (Docker services
        included); anywhere else -- local, Compose, tests -- it's "dev".
        """
        return os.environ.get("RENDER_GIT_COMMIT") or "dev"
