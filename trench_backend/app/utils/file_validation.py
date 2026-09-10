"""Validates an uploaded file's size and actual content type before it
touches storage or the database.

Sniffs content rather than trusting the filename extension: a PDF is only
accepted if it actually starts with the PDF magic bytes, etc.
"""

from fastapi import HTTPException, status

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB

_ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
}


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


def validate_upload(filename: str, content: bytes) -> str:
    """Validates size and content type.

    Args:
        filename: The uploaded file's original name (used only to
            disambiguate .docx from other zip-based formats, and to tell
            .txt from .md -- never trusted on its own).
        content: The raw file bytes.

    Returns:
        The sniffed mime type.

    Raises:
        HTTPException: 422 if the file is too large or an unsupported type.
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
    return mime_type
