"""Per-user rate limiting for chat message sends.

In-memory, fixed-window counter -- fine for a single-worker deployment, but
state isn't shared across processes, so running multiple uvicorn workers (or
replicas) would let each one grant its own 20/min independently. TODO: move
this to Redis (INCR + EXPIRE) once the app runs with more than one worker.
"""

import time
from collections import defaultdict

from fastapi import Depends, HTTPException, status

from app.database.models import User
from app.router.deps import get_current_user

MAX_REQUESTS_PER_WINDOW = 20
WINDOW_SECONDS = 60

# user_id -> (window_start_epoch_seconds, count_in_window)
_windows: dict[str, tuple[float, int]] = defaultdict(lambda: (0.0, 0))


def _check_and_record(user_id: str, *, now: float) -> int | None:
    """Returns None if the request is allowed, or the number of seconds
    until the window resets if the caller is over the limit."""
    window_start, count = _windows[user_id]
    if now - window_start >= WINDOW_SECONDS:
        _windows[user_id] = (now, 1)
        return None
    if count >= MAX_REQUESTS_PER_WINDOW:
        return int(WINDOW_SECONDS - (now - window_start)) + 1
    _windows[user_id] = (window_start, count + 1)
    return None


async def require_chat_rate_limit(
    current_user: User = Depends(get_current_user),
) -> User:
    """Caps a user to MAX_REQUESTS_PER_WINDOW chat message sends per
    WINDOW_SECONDS.

    Raises:
        HTTPException: 429, with a Retry-After header, once the caller has
            exceeded the limit within the current window.
    """
    retry_after = _check_and_record(str(current_user.id), now=time.monotonic())
    if retry_after is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many messages sent -- please slow down.",
            headers={"Retry-After": str(retry_after)},
        )
    return current_user
