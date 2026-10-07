import io

import pytest
from fastapi import HTTPException
from pypdf import PdfWriter

from app.utils.file_validation import (
    MAX_FILE_SIZE_BYTES,
    MAX_PDF_PAGES,
    validate_upload,
)

_DOCX_MAGIC = b"PK\x03\x04rest-of-a-zip"


def _make_pdf(num_pages: int) -> bytes:
    """A real, minimal, parseable PDF -- needed because validate_upload
    now actually opens and counts pages, not just sniffs magic bytes."""
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_accepts_pdf_by_magic_bytes():
    assert validate_upload("report.pdf", _make_pdf(1)) == "application/pdf"


def test_accepts_pdf_at_exactly_the_page_limit():
    assert validate_upload("report.pdf", _make_pdf(MAX_PDF_PAGES)) == "application/pdf"


def test_rejects_pdf_over_the_page_limit():
    with pytest.raises(HTTPException) as exc_info:
        validate_upload("report.pdf", _make_pdf(MAX_PDF_PAGES + 1))
    assert exc_info.value.status_code == 422
    assert str(MAX_PDF_PAGES + 1) in exc_info.value.detail


def test_rejects_corrupted_pdf():
    # Starts with the real magic bytes but isn't a structurally valid PDF
    # -- validate_upload must still reject it cleanly (422), not let
    # pypdf's own parse exception escape as a 500.
    with pytest.raises(HTTPException) as exc_info:
        validate_upload("report.pdf", b"%PDF-1.4\n...rest of a pdf...")
    assert exc_info.value.status_code == 422


def test_accepts_docx_by_magic_bytes_and_extension():
    mime_type = validate_upload("notes.docx", _DOCX_MAGIC)
    assert (
        mime_type
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_accepts_plain_text():
    assert validate_upload("notes.txt", b"hello world") == "text/plain"


def test_accepts_markdown():
    assert validate_upload("notes.md", b"# heading") == "text/markdown"


def test_rejects_oversized_file():
    with pytest.raises(HTTPException) as exc_info:
        validate_upload("big.txt", b"x" * (MAX_FILE_SIZE_BYTES + 1))
    assert exc_info.value.status_code == 422


def test_rejects_mismatched_content_and_extension():
    # .pdf extension but the content isn't actually a PDF.
    with pytest.raises(HTTPException) as exc_info:
        validate_upload("fake.pdf", b"just some text, not a real pdf")
    assert exc_info.value.status_code == 422


def test_rejects_unsupported_extension():
    with pytest.raises(HTTPException) as exc_info:
        validate_upload("archive.zip", _DOCX_MAGIC)
    assert exc_info.value.status_code == 422


def test_rejects_non_utf8_text_file():
    with pytest.raises(HTTPException) as exc_info:
        validate_upload("notes.txt", b"\xff\xfe\x00\x01invalid utf-8")
    assert exc_info.value.status_code == 422
