import time
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.database.models import User
from app.database.session import async_session_factory

PREFIX = "test-threads-router"


def _claims() -> dict:
    return {
        "uid": f"{PREFIX}-uid",
        "email": f"{PREFIX}@example.com",
        "iat": int(time.time()),
    }


async def _login(client: AsyncClient) -> None:
    with patch("app.utils.firebase.verify_firebase_id_token", return_value=_claims()):
        response = await client.post("/api/v1/auth/login", json={"id_token": "fake"})
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email.like(f"{PREFIX}%"))
        )
        for user in result.scalars().all():
            await session.delete(user)
        await session.commit()


@pytest.mark.asyncio
async def test_threads_endpoints_require_authentication(client: AsyncClient):
    response = await client.get("/api/v1/threads")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_list_get_and_delete_thread(client: AsyncClient):
    await _login(client)

    create_response = await client.post("/api/v1/threads")
    assert create_response.status_code == 200
    thread = create_response.json()

    list_response = await client.get("/api/v1/threads")
    assert list_response.status_code == 200
    assert len(list_response.json()["threads"]) == 1

    get_response = await client.get(f"/api/v1/threads/{thread['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["messages"] == []

    delete_response = await client.delete(f"/api/v1/threads/{thread['id']}")
    assert delete_response.status_code == 204

    list_after_delete = await client.get("/api/v1/threads")
    assert list_after_delete.json()["threads"] == []


@pytest.mark.asyncio
async def test_threads_sorted_most_recently_updated_first(client: AsyncClient):
    await _login(client)

    first = (await client.post("/api/v1/threads")).json()
    second = (await client.post("/api/v1/threads")).json()

    threads = (await client.get("/api/v1/threads")).json()["threads"]
    assert threads[0]["id"] == second["id"]
    assert threads[1]["id"] == first["id"]


@pytest.mark.asyncio
async def test_update_thread_title(client: AsyncClient):
    await _login(client)
    thread = (await client.post("/api/v1/threads")).json()

    response = await client.patch(
        f"/api/v1/threads/{thread['id']}", json={"title": "Renamed thread"}
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed thread"

    get_response = await client.get(f"/api/v1/threads/{thread['id']}")
    assert get_response.json()["thread"]["title"] == "Renamed thread"


@pytest.mark.asyncio
async def test_update_thread_title_rejects_empty_title(client: AsyncClient):
    await _login(client)
    thread = (await client.post("/api/v1/threads")).json()

    response = await client.patch(f"/api/v1/threads/{thread['id']}", json={"title": ""})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_thread_title_requires_ownership(client: AsyncClient):
    await _login(client)
    thread = (await client.post("/api/v1/threads")).json()

    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value={
            "uid": f"{PREFIX}-other-uid",
            "email": f"{PREFIX}-other@example.com",
            "iat": int(time.time()),
        },
    ):
        other_login = await client.post("/api/v1/auth/login", json={"id_token": "fake"})
    other_client_headers = {
        "Authorization": f"Bearer {other_login.json()['access_token']}"
    }

    response = await client.patch(
        f"/api/v1/threads/{thread['id']}",
        json={"title": "Hijacked"},
        headers=other_client_headers,
    )
    assert response.status_code == 404
