from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.jobs.app import app as procrastinate_app
from app.middleware.error_handler import register_error_handling
from app.router.auth import router as auth_router
from app.router.credentials import router as credentials_router
from app.router.documents import router as documents_router
from app.router.health import router as health_router
from app.router.users import router as users_router
from app.utils.logging import configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with procrastinate_app.open_async():
        yield


app = FastAPI(title="Trench API", lifespan=lifespan)

register_error_handling(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(credentials_router)
app.include_router(documents_router)
