"""Turn an accepted concern into exact, validated edits of the original extracted text.

A fix replaces whole sentences (so grammar around a flagged word can change) in the
original section coordinates used by the source map. Fixes that touch the same
sentence are merged into one edit, so the exported document never gets two
conflicting replacements for the same words.
"""
import re

from backend.ai.llm.client import LLMError
from backend.database.database import BackendError

SENTENCE_END = r"[.!?…][\"'”’)\]]*"
BREAKS = "\n\v\r"
MAX_REPLACEMENT = 4000


def sentence_unit(text, start, end):
    """Expand [start, end) to the sentence(s) it sits in, never crossing a line break."""
    left = 0
    for match in re.finditer(SENTENCE_END + r"(?=\s)", text[:start]):
        left = max(left, match.end())
    left = max([left] + [text.rfind(c, 0, start) + 1 for c in BREAKS])
    if re.search(SENTENCE_END + r"$", text[start:end]):
        right = end
    else:
        match = re.search(SENTENCE_END + r"(?=\s|$)", text[end:])
        right = end + match.end() if match else len(text)
    right = min([right] + [i for i in (text.find(c, end) for c in BREAKS) if i >= 0])
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    return left, right


def apply_text(text, edits):
    """edits: (start, end, replacement) in text coordinates, non-overlapping."""
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def _context(store, job_id, finding_id):
    job = store.job(job_id, include_result=True)
    if not job["is_final"] or job["result"] is None:
        raise BackendError("result_not_ready", "Fixes can be applied when the analysis has stopped.", 409)
    result = job["result"]
    finding = next((f for f in (result.get("analysis") or {}).get("findings", []) if f["finding_id"] == finding_id), None)
    if finding is None:
        raise BackendError("finding_not_found", "Finding does not belong to this job.", 404)
    extraction = store.document(job["upload_id"])["extraction"]
    sections = {s["section_id"]: s["text"] for s in (extraction.get("document") or {}).get("sections", [])}
    return result, finding, sections


def finding_spans(result, sections, finding):
    """Evidence positions in original sections, verified against the source text."""
    spans = []
    for evidence in finding.get("evidence", []):
        loc = evidence["location"]
        source = result.get("analysis_sources", {}).get(loc["section_id"], {})
        sid = source.get("original_section_id")
        if sid not in sections:
            continue
        start = source["source_start_char"] + loc["start_char"]
        end = source["source_start_char"] + loc["end_char"]
        if sections[sid][start:end] == evidence["original_content"]:
            spans.append((sid, start, end))
    return spans


def fix_units(result, sections, finding, edits):
    """Sentence units this finding would change, merged with overlapping applied edits."""
    units = {}
    for sid, start, end in finding_spans(result, sections, finding):
        units.setdefault(sid, []).append(sentence_unit(sections[sid], start, end))
    out = []
    for sid, ranges in units.items():
        merged = []
        for left, right in sorted(ranges):
            if merged and left < merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        section_edits = [e for e in edits if e["section_id"] == sid]
        for left, right in merged:
            included, changed = [], True
            while changed:
                changed = False
                for edit in section_edits:
                    if edit not in included and edit["start"] < right and edit["end"] > left:
                        included.append(edit)
                        left, right, changed = min(left, edit["start"]), max(right, edit["end"]), True
            text = sections[sid]
            current = apply_text(text[left:right], [(e["start"] - left, e["end"] - left, e["replacement"]) for e in included])
            finding_ids = sorted({finding["finding_id"], *(f for e in included for f in e["finding_ids"])})
            out.append({"section_id": sid, "start": left, "end": right, "original": text[left:right], "current": current,
                        "merged_edit_ids": [e["edit_id"] for e in included], "finding_ids": finding_ids})
    return sorted(out, key=lambda u: (u["section_id"], u["start"]))


def propose(store, job_id, finding_id, rewriter_factory):
    result, finding, sections = _context(store, job_id, finding_id)
    units = fix_units(result, sections, finding, store.edits(job_id))
    if not units:
        raise BackendError("fix_unavailable", "This concern's evidence could not be located in the document, so it cannot be fixed automatically.", 422)
    passages = {f"p{i}": u["current"] for i, u in enumerate(units, 1)}
    message = None
    try:
        rewrites = rewriter_factory().rewrite(passages, finding)
    except LLMError as error:
        # The educator can still type the corrected wording themselves.
        rewrites, message = passages, str(error)
        if error.code == "rewrite_unchanged" and all(u["merged_edit_ids"] for u in units):
            message = "This passage already includes a fix for another concern and the local AI found nothing more to change. Apply it to mark this concern as fixed too, or edit the wording"
    return {"finding_id": finding_id, "ai_rewrite": message is None, "message": message,
            "units": [{k: u[k] for k in ("section_id", "start", "end", "original", "current")}
                      | {"proposed": rewrites[f"p{i}"], "already_fixed": bool(u["merged_edit_ids"])}
                      for i, u in enumerate(units, 1)]}


def apply(store, job_id, finding_id, submitted, comment=""):
    result, finding, sections = _context(store, job_id, finding_id)
    units = {(u["section_id"], u["start"], u["end"]): u for u in fix_units(result, sections, finding, store.edits(job_id))}
    given = {(e["section_id"], e["start"], e["end"]): e["replacement"].strip() for e in submitted}
    bases = {(e["section_id"], e["start"], e["end"]): e["base"] for e in submitted}
    # A proposal made before another fix changed the same sentence must not overwrite it.
    if set(given) != set(units) or any(bases[k] != units[k]["current"] for k in units):
        raise BackendError("fix_outdated", "The document changed since this fix was prepared. Prepare the fix again.", 409)
    replaced, new_edits = [], []
    for key, unit in units.items():
        replacement = given[key]
        if not replacement or len(replacement) > MAX_REPLACEMENT:
            raise BackendError("invalid_fix", "Each corrected passage must contain text (at most 4,000 characters).", 422)
        if replacement == unit["current"] and not unit["merged_edit_ids"]:
            continue  # Unchanged passage: nothing to write.
        replaced += unit["merged_edit_ids"]
        new_edits.append({"section_id": unit["section_id"], "start": unit["start"], "end": unit["end"],
                          "original": unit["original"], "replacement": replacement, "finding_ids": unit["finding_ids"]})
    if not new_edits:
        raise BackendError("fix_unchanged", "Change the wording before applying the fix.", 422)
    store.apply_fix(job_id, finding_id, replaced, new_edits, {"decision": "accepted", "comment": comment, "fix_applied": True})
    return {"edits": store.edits(job_id), "feedback": store.feedback(job_id)}


def undo(store, job_id, finding_id):
    _context(store, job_id, finding_id)
    store.undo_fix(job_id, finding_id)
    return {"edits": store.edits(job_id), "feedback": store.feedback(job_id)}
