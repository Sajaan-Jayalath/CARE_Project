"""Read Office ZIP parts without unpacking files or resolving external links."""
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import ZipFile

from defusedxml.ElementTree import fromstring

from backend.extraction.common import ExtractionError, MAX_BYTES

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def package(data, extension):
    if data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise ExtractionError("encrypted_or_legacy_office", "Encrypted Office files and renamed legacy Office files are not supported. Save an unencrypted copy in the stated format.")
    archive = ZipFile(BytesIO(data))
    try:
        entries = archive.infolist()
        if len(entries) > 10000 or sum(i.file_size for i in entries) > 5 * MAX_BYTES:
            raise ExtractionError("archive_limit", "Office package exceeds safe expanded size or entry limits.")
        names = [i.filename for i in entries]
        if len(names) != len(set(names)) or any(".." in PurePosixPath(n).parts or n.startswith(("/", "\\")) or "\\" in n for n in names):
            raise ExtractionError("invalid_archive", "Office package contains duplicate or invalid paths.")
        if any(i.flag_bits & 1 for i in entries):
            raise ExtractionError("encrypted_document", "Password-protected documents are not supported.")
        expected = {"docx": "word/document.xml", "pptx": "ppt/presentation.xml", "xlsx": "xl/workbook.xml", "xlsm": "xl/workbook.xml"}[extension]
        if expected not in names or "[Content_Types].xml" not in names:
            raise ExtractionError("type_mismatch", "File contents do not match the filename extension.")
        # Validate every XML part before third-party parsers encounter it.
        for name in names:
            if name.endswith((".xml", ".rels")):
                fromstring(archive.read(name))
        return archive
    except Exception:
        archive.close()
        raise


def xml(archive, name):
    return fromstring(archive.read(name))


def warn_nontext(archive, out):
    media = [n for n in archive.namelist() if "/media/" in n and not n.endswith("/")]
    if media:
        out.warn("images_deferred", "Embedded images are not inspected in the text-only stage.", count=len(media))
    if any("/embeddings/" in n or "/diagrams/" in n for n in archive.namelist()):
        out.warn("objects_deferred", "Embedded objects or SmartArt may contain additional content; inspect them separately.")
