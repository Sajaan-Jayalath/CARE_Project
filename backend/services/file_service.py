"""Public extraction entry points. No uploads are persisted and no scripts run."""
import csv
import re
from io import StringIO
from pathlib import Path

from backend.extraction.common import Collector, ExtractionError, MAX_BYTES
from backend.extraction.office import package

SUPPORTED_FORMATS = {"pdf", "docx", "pptx", "xlsx", "xlsm", "xls", "txt", "md", "csv", "tsv"}


def add_paragraphs(out, text, kind, separator=r"\r?\n[ \t]*\r?\n"):
    start, paragraph = 0, 1
    ends = [m.end() for m in re.finditer(separator, text)] + [len(text)]
    for end in ends:
        if text[start:end].strip():
            out.add(text[start:end], kind=kind, paragraph=paragraph,
                    input_start_char=start, input_end_char=end)
            paragraph += 1
        start = end


def extract_text(text):
    if not isinstance(text, str) or not text.strip():
        raise ExtractionError("empty_text", "Enter some teaching text.")
    data = text.encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ExtractionError("file_too_large", "Input exceeds 20 MB.")
    out = Collector(data, "text.txt", "text")
    # Pasted text (e.g. from Word or a web page) marks paragraphs with single line breaks.
    # Following blank lines stay with their paragraph so sections still cover all input.
    add_paragraphs(out, text, "pasted_text", separator=r"\r?\n(?:[ \t]*\r?\n)*")
    return out.finish()


def extract_path(path):
    path = Path(path)
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    return extract_file(data, path.name)


def extract_file(data, filename):
    filename = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in SUPPORTED_FORMATS:
        raise ExtractionError("unsupported_format", "Supported text formats: PDF, DOCX, PPTX, XLSX, XLSM, XLS, TXT, MD, CSV and TSV. Images require the later OCR/vision stage.")
    if not data:
        raise ExtractionError("empty_file", "The uploaded file is empty.")
    if len(data) > MAX_BYTES:
        raise ExtractionError("file_too_large", "File exceeds 20 MB (20,000,000 bytes).")
    out = Collector(data, filename, extension)
    try:
        if extension == "pdf":
            from backend.extraction.pdf_extractor import extract
            extract(data, out)
        elif extension in {"docx", "pptx", "xlsx", "xlsm"}:
            with package(data, extension) as archive:
                if extension == "docx":
                    from backend.extraction.docx_extractor import extract
                    extract(archive, out)
                elif extension == "pptx":
                    from backend.extraction.pptx_extractor import extract
                    extract(data, archive, out)
                else:
                    from backend.extraction.excel_extractor import extract_xml
                    extract_xml(archive, out)
        elif extension == "xls":
            from backend.extraction.excel_extractor import extract_xls
            extract_xls(data, out)
        else:
            encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            text = data.decode(encoding)
            if "\x00" in text or any(ord(c) < 32 and c not in "\t\n\r\f" for c in text):
                raise ExtractionError("binary_text", "This file contains binary control characters rather than supported text.")
            if extension in {"csv", "tsv"}:
                csv.field_size_limit(1_000_000)
                for row, values in enumerate(csv.reader(StringIO(text, newline=""), delimiter="," if extension == "csv" else "\t", strict=True), 1):
                    for column, value in enumerate(values, 1):
                        out.add(value, row=row, column=column, kind="table_cell")
            else:
                add_paragraphs(out, text, "plain_text")
    except ExtractionError:
        raise
    except UnicodeError:
        raise ExtractionError("text_encoding", "Save plain-text files as UTF-8 or BOM-marked UTF-16.") from None
    except Exception:
        raise ExtractionError("invalid_document", "Document could not be read. It may be corrupted, encrypted, or contain unsupported structures.") from None
    return out.finish()
