import pytest
from fastapi import HTTPException

from app.utils.file_validation import MAX_FILE_SIZE_BYTES, validate_upload

_PDF_MAGIC = b"%PDF-1.4\n...rest of a pdf..."
_DOCX_MAGIC = b"PK\x03\x04rest-of-a-zip"


def test_accepts_pdf_by_magic_bytes():
    assert validate_upload("report.pdf", _PDF_MAGIC) == "application/pdf"


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
