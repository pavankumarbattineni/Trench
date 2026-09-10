"""Procrastinate task definitions -- thin wrappers; all real logic lives
in the corresponding *Service class so it's testable without a worker."""

import uuid

from app.database.session import async_session_factory
from app.jobs.app import app
from app.service.document_ingestion_service import DocumentIngestionService


@app.task(queue="documents")
async def process_document(document_id: str) -> None:
    async with async_session_factory() as session:
        await DocumentIngestionService.process(session, uuid.UUID(document_id))
