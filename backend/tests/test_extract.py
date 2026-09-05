"""Unit tests for app.extract's upload validation and text extraction.
No network calls. Real PDF/DOCX bytes are generated in-test with reportlab
and python-docx rather than using fixture files on disk.
"""

import io

import docx
import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.config import MAX_UPLOAD_BYTES
from app.extract import UnsupportedDocument, extract_text, sniff_content_type


def _make_pdf_with_text(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 750
    for line in text.split("\n"):
        c.drawString(72, y, line)
        y -= 14
    c.save()
    return buf.getvalue()


def _make_pdf_with_only_a_rectangle() -> bytes:
    """A real PDF, valid and parseable, but with no text layer at all --
    simulates a scanned/image-only document."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.rect(100, 100, 200, 200, fill=1)
    c.save()
    return buf.getvalue()


def _make_docx_with_text(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _make_png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buf, format="PNG")
    return buf.getvalue()


LONG_ENOUGH_TEXT = "This is a real operating procedure with enough characters to clear the two hundred character minimum extraction threshold. " * 2


def test_real_pdf_bytes_text_extracted():
    data = _make_pdf_with_text(LONG_ENOUGH_TEXT)
    result = extract_text(data)
    assert "operating procedure" in result


def test_real_docx_bytes_text_extracted():
    data = _make_docx_with_text([LONG_ENOUGH_TEXT, "A second paragraph with more content in it too."])
    result = extract_text(data)
    assert "operating procedure" in result
    assert "second paragraph" in result


def test_png_bytes_renamed_pdf_rejected_with_clear_error():
    """Content is sniffed, not the filename/extension -- extract_text takes
    only bytes, so there is no ".pdf" name to even honor. Real PNG bytes are
    rejected regardless of what a caller might have named the file."""
    data = _make_png()
    with pytest.raises(UnsupportedDocument):
        extract_text(data)


def test_random_binary_bytes_rejected():
    import os

    data = os.urandom(500)
    with pytest.raises(UnsupportedDocument):
        extract_text(data)


def test_zero_byte_file_rejected():
    with pytest.raises(UnsupportedDocument):
        extract_text(b"")


def test_pdf_with_no_text_layer_rejected_as_empty_or_scanned():
    data = _make_pdf_with_only_a_rectangle()
    with pytest.raises(UnsupportedDocument, match="empty or is a scanned image"):
        extract_text(data)


def test_file_over_10mb_rejected_before_parsing():
    data = b"a" * (MAX_UPLOAD_BYTES + 1)
    with pytest.raises(UnsupportedDocument, match="10 MB"):
        extract_text(data)


def test_utf8_text_with_bom_stripped():
    data = b"\xef\xbb\xbf" + LONG_ENOUGH_TEXT.encode("utf-8")
    result = extract_text(data)
    assert not result.startswith("﻿")
    assert result.startswith("This is a real operating procedure")


def test_latin1_encoded_text_is_extracted_not_rejected():
    """Documenting current behavior: sniff_content_type's non-printable-byte
    heuristic passes real latin-1 text (few/no control bytes), and
    extract_text falls back to a latin-1 decode after UTF-8 fails, so this
    is successfully extracted rather than rejected."""
    text = "Café procedure: " + LONG_ENOUGH_TEXT
    data = text.encode("latin-1")
    assert sniff_content_type(data) == "text"
    result = extract_text(data)
    assert "Café" in result
