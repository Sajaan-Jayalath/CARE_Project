export const categories = ['Gendered Language', 'Gender Stereotypes', 'Representation', 'Visual Representation', 'Professional Roles'];
export function locationLabel(source = {}, section = {}) {
  const parts = [];
  if (source.page || section.page) parts.push(`Page ${source.page || section.page}`);
  if (source.slide || section.slide) parts.push(`Slide ${source.slide || section.slide}`);
  if (source.sheet) parts.push(`Sheet ${source.sheet}`);
  if (source.cell) parts.push(`Cell ${source.cell}`);
  else if (source.row) parts.push(`Row ${source.row}${source.column ? `, column ${source.column}` : ''}`);
  if (source.table) parts.push(`Table ${source.table}`);
  if (source.paragraph || section.paragraph) parts.push(`Paragraph ${source.paragraph || section.paragraph}`);
  if (source.kind === 'speaker_notes') parts.push('Speaker notes');
  if (source.kind === 'image_text') parts.push(`Text in ${source.image_label?.toLowerCase() || 'an image'}`);
  if (!parts.length && source.preview_page_start) parts.push(`Text preview page ${source.preview_page_start}`);
  return parts.join(' · ') || `Passage ${section.section_id || ''}`;
}
// Why an assessment is incomplete: failed text windows, then each extraction warning with its pages.
export function incompleteReasons(result) {
  const chunks = result?.chunks || [];
  const failed = chunks.filter(c => c.status !== 'completed').length;
  const reasons = failed ? [`${failed} of ${chunks.length} part${chunks.length > 1 ? 's' : ''} of the text could not be fully analysed by the AI model.`] : [];
  const pages = new Map();
  for (const w of result?.extraction?.warnings || []) { if (!pages.has(w.message)) pages.set(w.message, []); if (w.page) pages.get(w.message).push(w.page); }
  for (const [message, list] of pages) reasons.push(list.length ? `${message} (page${list.length > 1 ? 's' : ''} ${list.join(', ')})` : message);
  return reasons;
}
export function mappedEvidence(finding, result) {
  const original = new Map((result?.extraction?.document?.sections || []).map(s => [s.section_id, s]));
  return (finding.evidence || []).flatMap(e => {
    const source = result.analysis_sources?.[e.location.section_id];
    if (!source) return [];
    const section = original.get(source.original_section_id);
    const start = source.source_start_char + e.location.start_char;
    const end = source.source_start_char + e.location.end_char;
    if (!section || Array.from(section.text).slice(start, end).join('') !== e.original_content) return [];
    return [{ sectionId: section.section_id, start, end, quote: e.original_content, label: locationLabel(source, section) }];
  });
}
// Section text as shown after applied fixes, with highlight ranges moved to match.
// Fixed passages are highlighted on their new wording; a concern whose words another
// fix rewrote is shown on that new wording too, in its own colour.
export function displayView(result, findings, edits = []) {
  const sections = result?.extraction?.document?.sections || [];
  const bySection = {};
  for (const e of edits) {
    const section = sections.find(s => s.section_id === e.section_id);
    if (section && Array.from(section.text).slice(e.start, e.end).join('') === e.original) (bySection[e.section_id] ||= []).push(e);
  }
  const shown = new Set(findings.map(f => f.finding_id));
  const shift = (items, pos) => items.filter(e => e.end <= pos).reduce((sum, e) => sum + Array.from(e.replacement).length - (e.end - e.start), 0);
  const texts = {}, ranges = {};
  for (const section of sections) {
    const items = (bySection[section.section_id] || []).slice().sort((a, b) => b.start - a.start);
    let chars = Array.from(section.text);
    for (const e of items) chars = [...chars.slice(0, e.start), ...Array.from(e.replacement), ...chars.slice(e.end)];
    texts[section.section_id] = chars.join('');
    for (const e of items) { const begin = e.start + shift(items, e.start); for (const id of e.finding_ids.filter(i => shown.has(i))) (ranges[section.section_id] ||= []).push({ start: begin, end: begin + Array.from(e.replacement).length, id }); }
  }
  for (const f of findings) for (const ev of mappedEvidence(f, result)) {
    const items = bySection[ev.sectionId] || [];
    const covering = items.filter(e => e.start < ev.end && e.end > ev.start);
    if (covering.length) {
      // Another concern's fix rewrote these words: show this concern on the new wording.
      for (const e of covering) if (!e.finding_ids.includes(f.finding_id)) { const begin = e.start + shift(items, e.start); (ranges[ev.sectionId] ||= []).push({ start: begin, end: begin + Array.from(e.replacement).length, id: f.finding_id }); }
      continue;
    }
    const s = shift(items, ev.start);
    (ranges[ev.sectionId] ||= []).push({ ...ev, start: ev.start + s, end: ev.end + s, id: f.finding_id });
  }
  return { texts, ranges };
}
export function textPieces(text, ranges) {
  const chars = Array.from(text);
  const positions = [...new Set([0, chars.length, ...ranges.flatMap(r => [r.start, r.end])])].sort((a, b) => a - b);
  return positions.slice(0, -1).map((start, i) => ({ text: chars.slice(start, positions[i + 1]).join(''), ids: [...new Set(ranges.filter(r => r.start < positions[i + 1] && r.end > start).map(r => r.id))] }));
}
export const humanise = value => (value || '').replaceAll('_', ' ');
