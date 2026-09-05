"""Upload validation and in-memory text extraction for the four accepted
document types. Never writes an upload to disk and never uses a client-
supplied filename in a filesystem path; extraction works from raw bytes only.
"""

import re
import zipfile
from dataclasses import dataclass
from io import BytesIO

import docx
from pypdf import PdfReader

from app.config import CHUNK_TARGET_WORDS, MAX_UPLOAD_BYTES, MIN_EXTRACTED_CHARS


class UnsupportedDocument(ValueError):
    """Raised when an upload fails validation or extraction. Message is user-facing."""


@dataclass
class DocChunk:
    text: str
    chunk_index: int


def sniff_content_type(data: bytes) -> str:
    """Identify the file type from its bytes, not its filename or extension."""
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(BytesIO(data)) as zf:
                if "word/document.xml" in zf.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            pass
        raise UnsupportedDocument("File could not be recognized as a PDF, DOCX, or text document.")
    try:
        data.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        pass
    # latin-1 maps every byte 0-255 to a character, so decode() alone can
    # never raise UnicodeDecodeError here -- it cannot distinguish legacy-
    # encoded text from arbitrary binary data (an image, an executable, a
    # corrupt file). Reject anything with more than a token amount of
    # non-printable/control bytes, which real text files do not contain.
    non_printable = sum(1 for b in data if b < 9 or (13 < b < 32) or b == 127)
    if data and non_printable / len(data) > 0.01:
        raise UnsupportedDocument("File could not be recognized as a PDF, DOCX, or text document.")
    return "text"


def pdf_reader_to_text(reader: PdfReader) -> str:
    """Join every page's extracted text. Shared with ingest.py's offline extraction."""
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def normalize_whitespace(text: str) -> str:
    """Collapse run of spaces/tabs and excess blank lines. Shared with ingest.py."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(data: bytes) -> str:
    """Validate and extract text from an upload's raw bytes, in memory only."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise UnsupportedDocument("File exceeds the 10 MB upload limit.")

    content_type = sniff_content_type(data)
    if content_type == "pdf":
        try:
            text = pdf_reader_to_text(PdfReader(BytesIO(data)))
        except Exception as exc:  # noqa: BLE001 - pypdf raises many exception types for a malformed PDF
            raise UnsupportedDocument("File could not be read as a valid PDF document.") from exc
    elif content_type == "docx":
        try:
            document = docx.Document(BytesIO(data))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        except Exception as exc:  # noqa: BLE001 - python-docx/lxml raise many exception types for a malformed DOCX
            raise UnsupportedDocument("File could not be read as a valid DOCX document.") from exc
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")

    text = normalize_whitespace(text)

    if len(text) < MIN_EXTRACTED_CHARS:
        raise UnsupportedDocument("Document appears empty or is a scanned image.")

    return text


def chunk_text(text: str) -> list[DocChunk]:
    """Group paragraphs into chunks of roughly CHUNK_TARGET_WORDS words each."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks: list[DocChunk] = []
    current_words: list[str] = []
    for paragraph in paragraphs:
        current_words.extend(paragraph.split())
        if len(current_words) >= CHUNK_TARGET_WORDS:
            chunks.append(DocChunk(text=" ".join(current_words), chunk_index=len(chunks)))
            current_words = []
    if current_words:
        chunks.append(DocChunk(text=" ".join(current_words), chunk_index=len(chunks)))

    return chunks
