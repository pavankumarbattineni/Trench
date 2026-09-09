import pytest
from sqlalchemy import text

from app.database.session import async_session_factory


@pytest.mark.asyncio
async def test_session_can_execute_select_1():
    async with async_session_factory() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
