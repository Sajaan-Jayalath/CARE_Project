"""Tesseract OCR with exact text/token offsets and isolated per-image failures."""
from io import BytesIO
import re
import shutil
import threading
import time
from pathlib import Path

from PIL import Image
from backend.ai.schemas.image_schema import OCRResult, OCRToken, PixelBox

# pytesseract exposes a process-global executable setting. Keep independent
# upload threads from swapping it while a subprocess is running.
_tesseract_lock = threading.Lock()


def parse_data(data, image_id, width, height):
    words, tokens, previous = [], [], None
    cursor = 0
    count = len(data["text"])
    for i in range(count):
        text = str(data["text"][i]).strip()
        confidence = float(data["conf"][i])
        if not text or confidence < 0:
            continue
        line = tuple(data[key][i] for key in ("page_num", "block_num", "par_num", "line_num"))
        separator = "" if not words else " " if line == previous else "\n"
        words.append(separator)
        cursor += len(separator)
        box = PixelBox(x=int(data["left"][i]), y=int(data["top"][i]),
                       width=int(data["width"][i]), height=int(data["height"][i]))
        if box.x + box.width > width or box.y + box.height > height:
            raise ValueError("OCR box lies outside source image")
        tokens.append(OCRToken(text=text, start_char=cursor, end_char=cursor + len(text),
                               bbox=box, recognition_confidence=confidence))
        words.append(text)
        cursor += len(text)
        previous = line
    text = "".join(words)
    return OCRResult(image_id=image_id, status="completed" if text else "no_text", text=text, tokens=tokens)


class TesseractOCR:
    def __init__(self, settings):
        self.settings = settings

    def recognise(self, data, occurrence):
        try:
            import pytesseract
            executable = self.settings.ocr_executable
            if not shutil.which(executable) and not Path(executable).is_file():
                return OCRResult(image_id=occurrence.image_id, status="failed", error_code="ocr_unavailable")
            with _tesseract_lock:
                previous = pytesseract.pytesseract.tesseract_cmd
                try:
                    pytesseract.pytesseract.tesseract_cmd = executable
                    with Image.open(BytesIO(data)) as image:
                        left, top, right, bottom = occurrence.location.get("crop", [0, 0, 0, 0])
                        x0 = max(0, min(image.width, round(left * image.width)))
                        y0 = max(0, min(image.height, round(top * image.height)))
                        x1 = max(0, min(image.width, round((1 - right) * image.width)))
                        y1 = max(0, min(image.height, round((1 - bottom) * image.height)))
                        if x1 <= x0 or y1 <= y0:
                            raise ValueError("Image crop has no visible pixels")
                        visible = image.crop((x0, y0, x1, y1)) if (x0, y0, x1, y1) != (0, 0, image.width, image.height) else image
                        result = pytesseract.image_to_data(visible, lang=self.settings.ocr_language,
                            output_type=pytesseract.Output.DICT, timeout=self.settings.ocr_timeout_seconds)
                        parsed = parse_data(result, occurrence.image_id, visible.width, visible.height)
                        for token in parsed.tokens:
                            token.bbox.x += x0
                            token.bbox.y += y0
                    return parsed
                finally:
                    pytesseract.pytesseract.tesseract_cmd = previous
        except ImportError:
            code = "ocr_dependency_missing"
        except RuntimeError:
            code = "ocr_timeout_or_engine_error"
        except Exception:
            code = "ocr_error"
        return OCRResult(image_id=occurrence.image_id, status="failed", error_code=code)


def normalized(text):
    # Comparison only: never rewrite canonical source text or token offsets.
    return " ".join(text.casefold().split())


def line_ranges(text):
    yield from ((m.start(), m.end()) for m in re.finditer(r"[^\n]+", text) if m.group().strip())


def integrate_ocr(batch, engine=None):
    out = batch.out
    cache = {}
    engine = engine or TesseractOCR(batch.settings)
    direct = list(out.sections)
    deadline = time.monotonic() + batch.settings.ocr_document_timeout_seconds
    for image in out.images:
        cache_key = (image.asset_sha256, tuple(image.location.get("crop", [0, 0, 0, 0])))
        if not batch.settings.ocr_enabled or time.monotonic() >= deadline:
            result = OCRResult(image_id=image.image_id, status="skipped")
        elif cache_key in cache:
            result = cache[cache_key].model_copy(deep=True, update={"image_id": image.image_id})
        else:
            try:
                result = OCRResult.model_validate(engine.recognise(batch.assets[image.asset_sha256], image))
                if result.image_id != image.image_id:
                    raise ValueError("OCR returned a different image")
                if any(t.bbox.x + t.bbox.width > image.width or t.bbox.y + t.bbox.height > image.height for t in result.tokens):
                    raise ValueError("OCR coordinates outside source image")
                if result.section_ids or result.duplicate_section_ids:
                    raise ValueError("OCR engine cannot assign source sections")
            except Exception:
                result = OCRResult(image_id=image.image_id, status="failed", error_code="ocr_invalid_response")
            cache[cache_key] = result.model_copy(deep=True)
        if result.status == "completed":
            if result.tokens and sum(t.recognition_confidence for t in result.tokens) / len(result.tokens) < 50:
                out.warn("ocr_low_quality", "OCR recognition confidence is low; verify the extracted wording against the image.", image_id=image.image_id)
            candidates = [s for s in direct if s.section_id in image.context_section_ids]
            # Whole exact asset duplicates on one page/slide can share their OCR
            # section; copies on different pages remain independent evidence.
            for earlier in out.images[:image.image_index - 1]:
                if earlier.asset_sha256 == image.asset_sha256 and earlier.location.get("crop", [0, 0, 0, 0]) == image.location.get("crop", [0, 0, 0, 0]) and ((image.page and image.page == earlier.page) or (image.slide and image.slide == earlier.slide)):
                    prior = next((r for r in out.ocr_results if r.image_id == earlier.image_id), None)
                    if prior:
                        result.duplicate_section_ids = prior.section_ids + prior.duplicate_section_ids
                        for sid in result.duplicate_section_ids:
                            source = out.sources[sid]
                            source.setdefault("related_image_ids", []).append(image.image_id)
                        break
            if not result.duplicate_section_ids:
                for start, end in line_ranges(result.text):
                    text = result.text[start:end]
                    value = normalized(text)
                    # Require word boundaries and a substantial match for partial
                    # lines. Short one-word hits must match an entire source unit.
                    duplicates = [s.section_id for s in candidates if value == normalized(s.text) or
                                  (len(value) >= 12 and len(value.split()) >= 2 and
                                   (" " + value + " ") in (" " + normalized(s.text) + " "))]
                    if duplicates:
                        result.duplicate_section_ids.extend(duplicates)
                        continue
                    tokens = [t.model_dump(mode="json") for t in result.tokens if t.start_char >= start and t.end_char <= end]
                    for token in tokens:
                        token["start_char"] -= start
                        token["end_char"] -= start
                    before = len(out.sections)
                    try:
                        out.add(text, section_type="ocr_text", kind="ocr_text", image_id=image.image_id,
                                page=image.page, slide=image.slide,
                                image_index=image.image_index, source_document_type=image.source_type,
                                image_location=image.location, ocr_start_char=start, ocr_end_char=end,
                                ocr_tokens=tokens, recognition_engine=result.engine)
                    except Exception:
                        out.warn("ocr_content_limit", "OCR text exceeded document limits; remaining OCR text was skipped.", image_id=image.image_id)
                        break
                    if len(out.sections) > before:
                        result.section_ids.append(out.sections[-1].section_id)
        elif result.status in {"failed", "skipped", "no_text"}:
            code = result.error_code or (("ocr_disabled" if not batch.settings.ocr_enabled else "ocr_time_budget") if result.status == "skipped" else "ocr_no_text")
            out.warn(code, "OCR was unavailable, disabled, or found no readable text; review this image.", image_id=image.image_id, page=image.page, slide=image.slide)
        out.ocr_results.append(result)
    # Keep OCR adjacent to its page/slide or Word paragraph context. Source IDs
    # and exact strings stay intact; only new OCR sections are interleaved.
    if out.images and any(s.type == "ocr_text" for s in out.sections):
        place_ocr_sections(out)
    if out.images:
        out.warn("visual_analysis_deferred", "Images were extracted; visual interpretation is not implemented in this phase.")


def place_ocr_sections(out):
    direct = [s for s in out.sections if s.type != "ocr_text"]
    buckets = {i: [] for i in range(len(direct) + 1)}
    for section in out.sections:
        if section.type != "ocr_text":
            continue
        source = out.sources[section.section_id]
        location = source["image_location"]
        anchor = 0
        for i, candidate in enumerate(direct, 1):
            candidate_source = out.sources[candidate.section_id]
            if section.page:
                eligible = candidate.page is not None and candidate.page <= section.page
            elif section.slide:
                eligible = candidate.slide is not None and candidate.slide <= section.slide
            else:
                eligible = (candidate_source.get("part") == location.get("part") and
                            candidate.paragraph is not None and
                            candidate.paragraph <= (location.get("paragraph") or 0) + 1)
            if eligible:
                anchor = i
        buckets[anchor].append(section)
    ordered = list(buckets[0])
    for i, section in enumerate(direct, 1):
        ordered.append(section)
        ordered.extend(buckets[i])
    out.sections = ordered
    if out.page_basis.startswith("generated"):
        from backend.extraction.common import PREVIEW_PAGE_CHARS
        cursor = 0
        for section in out.sections:
            out.sources[section.section_id].update(preview_start_char=cursor,
                preview_page_start=cursor // PREVIEW_PAGE_CHARS + 1,
                preview_page_end=(cursor + len(section.text) - 1) // PREVIEW_PAGE_CHARS + 1)
            cursor += len(section.text)
