from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database.checkpointer import close_checkpointer, init_checkpointer
from app.middleware.error_handler import register_error_handling
from app.router.auth import router as auth_router
from app.router.chat import router as chat_router
from app.router.config import router as config_router
from app.router.credentials import router as credentials_router
from app.router.documents import router as documents_router
from app.router.health import router as health_router
from app.router.organizations import router as organizations_router
from app.router.threads import router as threads_router
from app.router.users import router as users_router
from app.utils.logging import configure_logging
from app.utils.tracing import configure_tracing

configure_tracing()
configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_checkpointer()
    try:
        yield
    finally:
        await close_checkpointer()


app = FastAPI(title="Trench API", lifespan=lifespan)

register_error_handling(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# /health is intentionally left unversioned/unprefixed -- load balancers
# and uptime probes conventionally hit a fixed, stable health path rather
# than one that moves with the API version.
app.include_router(health_router)

_API_V1_PREFIX = "/api/v1"
app.include_router(auth_router, prefix=_API_V1_PREFIX)
app.include_router(users_router, prefix=_API_V1_PREFIX)
app.include_router(credentials_router, prefix=_API_V1_PREFIX)
app.include_router(documents_router, prefix=_API_V1_PREFIX)
app.include_router(organizations_router, prefix=_API_V1_PREFIX)
app.include_router(config_router, prefix=_API_V1_PREFIX)
app.include_router(threads_router, prefix=_API_V1_PREFIX)
app.include_router(chat_router, prefix=_API_V1_PREFIX)
