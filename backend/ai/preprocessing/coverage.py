"""Original-source coverage, independent of repeated overlap windows."""


def coverage_report(extraction, document=None, sources=None, layers=None, records=()):
    sources, layers = sources or {}, layers or {}
    original = extraction.document.sections if extraction.document else []
    segments = {s.section_id: s for s in document.sections} if document else {}
    by_original = {}
    for identifier, source in sources.items():
        by_original.setdefault(source["original_section_id"], []).append(identifier)
    reports = {}
    for name in ("rules", "nlp", "llm"):
        layer = layers.get(name)
        analysed = set(layer.analysed_section_ids) if layer else set()
        sections = []
        for section in original:
            ids = by_original.get(section.section_id, [])
            processed = [i for i in ids if i in analysed]
            missing = [i for i in ids if i not in analysed]
            def spans(items):
                return [{"segment_id": i, "start_char": sources[i]["source_start_char"],
                         "end_char": sources[i]["source_end_char"]} for i in items]
            sections.append({"section_id": section.section_id,
                "status": "completed" if ids and not missing else "partial" if processed else "unprocessed",
                "processed_ranges": spans(processed), "unprocessed_ranges": spans(missing),
                "source": extraction.sources.get(section.section_id, {})})
        reports[name] = {"analysed_segments": len(analysed), "total_segments": len(segments),
            "processed_characters": sum(len(segments[i].text) for i in analysed if i in segments),
            "unprocessed_segment_ids": [i for i in segments if i not in analysed], "sections": sections}
    # Whitespace-only tails are not inference omissions; expose them explicitly.
    whitespace = []
    for section in original:
        cursor = 0
        for i in by_original.get(section.section_id, []):
            start, end = sources[i]["source_start_char"], sources[i]["source_end_char"]
            if cursor < start:
                whitespace.append({"section_id": section.section_id, "start_char": cursor, "end_char": start})
            cursor = end
        if cursor < len(section.text) and not section.text[cursor:].strip():
            whitespace.append({"section_id": section.section_id, "start_char": cursor, "end_char": len(section.text)})
    return {"scope": "extracted selectable text; no OCR or visual assessment",
        "total_original_sections": len(original), "total_segments": len(segments),
        "total_segment_characters": sum(len(s.text) for s in segments.values()),
        "planned_chunks": len(records), "failed_or_unprocessed_chunks": [r["chunk"] for r in records if r["status"] != "completed"],
        "layers": reports, "excluded_whitespace_ranges": whitespace,
        "extraction_warnings": extraction.warnings,
        "complete_extraction": extraction.status == "completed"}
