"""Validates an uploaded file's size and actual content type before it
touches storage or the database.

Sniffs content rather than trusting the filename extension: a PDF is only
accepted if it actually starts with the PDF magic bytes, etc.
"""

import io

from fastapi import HTTPException, status
from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB

# PDF only -- DOCX has no stored page count (pagination is a rendering-time
# concern decided by page size/margins/fonts, not something in the file
# itself), so there's nothing exact to check without first rendering it.
MAX_PDF_PAGES = 30

_ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
}

_DOCUMENT_TYPE_BY_MIME = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
    "text/markdown": "md",
}


def document_type_for(mime_type: str) -> str:
    """Maps a sniffed mime type to the short category stored on Document."""
    return _DOCUMENT_TYPE_BY_MIME[mime_type]


def _sniff_mime_type(filename: str, content: bytes) -> str | None:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"PK\x03\x04") and filename.lower().endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if filename.lower().endswith((".txt", ".md")):
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return "text/markdown" if filename.lower().endswith(".md") else "text/plain"
    return None


def _enforce_pdf_page_limit(content: bytes) -> None:
    try:
        page_count = len(PdfReader(io.BytesIO(content)).pages)
    except PdfReadError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Could not read this PDF -- it may be corrupted.",
        ) from exc
    if page_count > MAX_PDF_PAGES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"This PDF has {page_count} pages -- the limit is "
            f"{MAX_PDF_PAGES} pages.",
        )


def validate_upload(filename: str, content: bytes) -> str:
    """Validates size, content type, and (PDF only) page count.

    Args:
        filename: The uploaded file's original name (used only to
            disambiguate .docx from other zip-based formats, and to tell
            .txt from .md -- never trusted on its own).
        content: The raw file bytes.

    Returns:
        The sniffed mime type.

    Raises:
        HTTPException: 422 if the file is too large, an unsupported type,
            an unreadable/corrupted PDF, or a PDF over MAX_PDF_PAGES pages
            -- checked here, synchronously, before the file is stored or a
            Document row is created, so an oversized PDF is never even
            queued for the (separate, async) parsing/ingestion pipeline.
    """
    if len(content) > MAX_FILE_SIZE_BYTES:
        limit_mb = MAX_FILE_SIZE_BYTES // (1024 * 1024)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"File is too large -- the limit is {limit_mb} MB.",
        )
    mime_type = _sniff_mime_type(filename, content)
    if mime_type is None or mime_type not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Unsupported file type. Trench accepts PDF, DOCX, TXT, and Markdown files.",
        )
    if mime_type == "application/pdf":
        _enforce_pdf_page_limit(content)
    return mime_type
