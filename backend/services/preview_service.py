"""Render original documents locally and anchor validated evidence on their pages."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unicodedata

import pymupdf

from backend.database.database import BackendError

OFFICE = {"docx", "pptx", "xlsx", "xlsm", "xls"}
conversion_lock = threading.Lock()


def office_executable():
    candidates = [os.getenv("CARE_LIBREOFFICE_PATH"), shutil.which("soffice"),
                  r"C:\Program Files\LibreOffice\program\soffice.exe",
                  "/Applications/LibreOffice.app/Contents/MacOS/soffice"]
    return next((p for p in candidates if p and Path(p).is_file()), None)


def preview_pdf(store, document):
    fmt = document["extraction"]["format"]
    if fmt == "pdf":
        return document["original"]
    if fmt not in OFFICE:
        raise BackendError("preview_text_only", "This input uses the text preview.", 422)
    executable = office_executable()
    if not executable:
        raise BackendError("preview_renderer_missing", "Install LibreOffice locally to preview Office documents, then retry. Text review is still available.", 503)
    with store.connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS document_previews (upload_id TEXT PRIMARY KEY REFERENCES documents(upload_id) ON DELETE CASCADE, pdf BLOB NOT NULL)")
        cached = db.execute("SELECT pdf FROM document_previews WHERE upload_id=?", (document["upload_id"],)).fetchone()
    if cached:
        return cached[0]
    if not conversion_lock.acquire(timeout=100):
        raise BackendError("preview_busy", "Another document preview is being prepared. Retry shortly.", 503)
    try:
        # A concurrent request may have finished while this request was waiting.
        with store.connection() as db:
            cached = db.execute("SELECT pdf FROM document_previews WHERE upload_id=?", (document["upload_id"],)).fetchone()
        if cached:
            return cached[0]
        with tempfile.TemporaryDirectory(prefix="care-preview-") as directory:
            root = Path(directory)
            profile = root / "profile"
            profile.mkdir()
            # Never run document macros or update linked content automatically.
            (profile / "user").mkdir()
            (profile / "user" / "registrymodifications.xcu").write_text(
                '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
                '<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
                '<item oor:path="/org.openoffice.Office.Common/Security"><prop oor:name="UpdateLinks" oor:op="fuse"><value>0</value></prop></item></oor:items>', encoding="utf-8")
            source = root / ("document." + fmt)
            source.write_bytes(document["original"])
            try:
                subprocess.run([executable, "-env:UserInstallation=" + profile.as_uri(), "--headless", "--nologo", "--nodefault", "--norestore",
                                "--convert-to", "pdf", "--outdir", str(root), str(source)],
                               timeout=90, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            except (subprocess.SubprocessError, OSError) as error:
                raise BackendError("preview_conversion_failed", "Document rendering failed or exceeded 90 seconds. Use text preview or upload an exported PDF.", 422) from error
            output = root / "document.pdf"
            if not output.is_file() or output.stat().st_size > 50_000_000:
                raise BackendError("preview_conversion_failed", "The document could not be rendered within the preview size limit.", 422)
            data = output.read_bytes()
            with pymupdf.open(stream=data, filetype="pdf") as pdf:
                if len(pdf) > 100:
                    raise BackendError("preview_page_limit", "The rendered document exceeds 100 pages. Use text preview or export a shorter PDF.", 422)
            with store.connection() as db:
                # Deleting an upload during conversion must not recreate its cache.
                db.execute("INSERT OR REPLACE INTO document_previews SELECT upload_id, ? FROM documents WHERE upload_id=?", (data, document["upload_id"]))
            return data
    finally:
        conversion_lock.release()


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text) if not c.isspace() and c != "\u00ad")


def page_characters(page):
    characters = []
    for block in page.get_text("rawdict", sort=True, flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]:
        for line_no, line in enumerate(block.get("lines", [])):
            for span in line["spans"]:
                for char in span["chars"]:
                    rect = pymupdf.Rect(char["bbox"])
                    for letter in normalized(char["c"]):
                        characters.append((letter, rect, (block["number"], line_no)))
    return characters


def preview_manifest(store, job_id):
    job = store.job(job_id, include_result=True)
    document = store.document(job["upload_id"], original=True)
    result = job.get("result") or {}
    extraction = document["extraction"]
    sections = {s["section_id"]: s for s in (extraction.get("document") or {}).get("sections", [])}
    analysis = result.get("analysis") or {}
    findings = analysis.get("findings", []) + (analysis.get("document_analysis") or {}).get("findings", [])
    data = preview_pdf(store, document)
    overlays, unmapped = [], set()
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        pages = [{"number": i + 1, "width": p.rect.width, "height": p.rect.height} for i, p in enumerate(pdf)]
        chars = [page_characters(p) for p in pdf] if findings else []
        anchors = {}
        for finding in findings:
            for evidence in finding.get("evidence", []):
                loc = evidence["location"]
                source = result.get("analysis_sources", {}).get(loc["section_id"], {})
                sid = source.get("original_section_id")
                section = sections.get(sid)
                if not section:
                    unmapped.add(finding["finding_id"])
                    continue
                start = source["source_start_char"] + loc["start_char"]
                end = source["source_start_char"] + loc["end_char"]
                if section["text"][start:end] != evidence["original_content"]:
                    unmapped.add(finding["finding_id"])
                    continue
                if sid not in anchors:
                    original_source = extraction["sources"].get(sid, {})
                    page_no = original_source.get("page") if extraction["format"] == "pdf" else original_source.get("slide")
                    sequence = []
                    if original_source.get("kind") != "speaker_notes":
                        for i, page_chars in enumerate(chars):
                            if page_no and page_no != i + 1:
                                continue
                            for letter, rect, line in page_chars:
                                if extraction["format"] == "pdf" and original_source.get("bbox"):
                                    clip = pymupdf.Rect(original_source["bbox"])
                                    if not clip.contains((rect.tl + rect.br) / 2):
                                        continue
                                sequence.append((letter, i, rect, line))
                    haystack = "".join(c[0] for c in sequence)
                    needle = normalized(section["text"])
                    pos = haystack.find(needle) if needle else -1
                    # Do not guess between repeated paragraphs/cells in converted files.
                    anchors[sid] = sequence[pos:pos + len(needle)] if pos >= 0 and haystack.find(needle, pos + 1) < 0 else []
                anchor = anchors[sid]
                a, b = len(normalized(section["text"][:start])), len(normalized(section["text"][:end]))
                groups = {}
                for _, index, rect, line in anchor[a:b]:
                    key = (index, line)
                    groups[key] = groups[key] | rect if key in groups else rect
                if not groups:
                    unmapped.add(finding["finding_id"])
                for (index, _), rect in groups.items():
                    rotated = rect * pdf[index].rotation_matrix
                    bounds = pdf[index].rect
                    overlays.append({"finding_id": finding["finding_id"], "section_id": sid, "page": index + 1,
                                     "rect": [rotated.x0 / bounds.width, rotated.y0 / bounds.height, rotated.width / bounds.width, rotated.height / bounds.height]})
    return {"upload_id": document["upload_id"], "format": extraction["format"], "pages": pages, "highlights": overlays,
            "unmapped_finding_ids": sorted(unmapped), "converted": extraction["format"] != "pdf"}


def page_image(store, upload_id, number):
    document = store.document(upload_id, original=True)
    with pymupdf.open(stream=preview_pdf(store, document), filetype="pdf") as pdf:
        if number < 1 or number > len(pdf):
            raise BackendError("preview_page_not_found", "This preview page does not exist.", 404)
        page = pdf[number - 1]
        scale = min(2, 1600 / max(page.rect.width, page.rect.height))
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
