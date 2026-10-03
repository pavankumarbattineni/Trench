"""Centralized, consistent error handling for Trench.

Every failure mode -- a raised HTTPException, a Pydantic request-validation
error, a domain ValueError, or a genuinely unexpected exception -- resolves
to the same ErrorResponse JSON shape: {status_code, status, message}.

Two mechanisms are needed, not one, because of how Starlette actually
dispatches exceptions. FastAPI installs its own default handlers for
HTTPException and RequestValidationError inside its ASGI exception
middleware, which sits closer to the router than any `app.add_middleware`
middleware -- so a BaseHTTPMiddleware wrapping call_next() never sees those
two exception types; they're already converted into a response before
they'd propagate that far out. (Verified empirically against a real
uvicorn server: a bare `except HTTPException` inside BaseHTTPMiddleware.
dispatch() never fires.) Those two are therefore handled via
`add_exception_handler`, the mechanism that actually intercepts them.
Anything else -- a raw ValueError, or a truly unhandled exception -- has no
default handler and genuinely does propagate up through call_next(), so a
middleware safety net is exactly the right tool there.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.schemas.error import ErrorResponse

logger = logging.getLogger(__name__)


def _error_response(status_code: int, error_status: str, message: str) -> JSONResponse:
    body = ErrorResponse(status_code=status_code, status=error_status, message=message)
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return _error_response(exc.status_code, exc.__class__.__name__, str(exc.detail))


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    messages = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    )
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "ValidationError", messages
    )


class UnhandledExceptionMiddleware(BaseHTTPMiddleware):
    """Safety net for exceptions FastAPI doesn't special-case.

    HTTPException and RequestValidationError never reach here -- see the
    module docstring. This only ever sees a raw domain ValueError or a
    genuinely unexpected exception.
    """

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except ValueError as exc:
            return _error_response(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "UnprocessableContent", str(exc)
            )
        except Exception:
            logger.exception(
                "Unhandled exception | path=%s method=%s",
                request.url.path,
                request.method,
            )
            return _error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "InternalServerError",
                "Something went wrong. Please try again later.",
            )


def register_error_handling(app: FastAPI) -> None:
    """Wires up every layer of Trench's centralized error handling."""
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_middleware(UnhandledExceptionMiddleware)
