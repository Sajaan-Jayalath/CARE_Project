import { useMemo, useState, useEffect } from 'react';
import { locationLabel, displayView, textPieces } from '../utils/formatters';
import RenderedPreview, { SeverityLegend, highlightSeverity } from './RenderedPreview';

export default function DocumentPreview(props) {
  const [textView, setTextView] = useState(false);
  const format = props.result?.extraction?.format;
  const canRender = props.jobId && ['pdf', 'docx', 'pptx', 'xlsx', 'xlsm', 'xls', 'png', 'jpg', 'jpeg', 'webp', 'bmp', 'gif', 'tif', 'tiff'].includes(format);
  return <div className="preview-container">
    {canRender && textView && <button className="text-button" onClick={() => setTextView(false)}>Back to document view</button>}
    {canRender && !textView ? <RenderedPreview {...props} onTextView={() => setTextView(true)} /> : <TextPreview {...props} />}
  </div>;
}

function TextPreview({ result, findings, fixed, edits, selected, target, onSelect }) {
  const sections = result?.extraction?.document?.sections || [];
  const [page, setPage] = useState(0);
  const count = Math.max(1, Math.ceil(sections.length / 20));
  // Show the corrected wording once fixes are applied, with highlights moved to match.
  const { texts, ranges } = useMemo(() => displayView(result, findings, edits), [result, findings, edits]);
  useEffect(() => {
    if (!target) return;
    const index = sections.findIndex(s => s.section_id === target.sectionId);
    if (index < 0) return;
    setPage(Math.floor(index / 20));
    const timer = setTimeout(() => { const element = document.getElementById(`passage-${target.sectionId}`); element?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); element?.focus({ preventScroll: true }); }, 50);
    return () => clearTimeout(timer);
  }, [target, sections]);
  return <section className="panel preview-panel" aria-labelledby="preview-title"><div className="panel-heading"><h2 id="preview-title">Preview of the Input</h2><span className="small-tag">Extracted text</span></div><p className="muted small">Select a colored highlight to review its concerns.</p><SeverityLegend /><div className="document-scroll">{!sections.length ? <div className="empty-state">No selectable text is available. Scanned or image-only content needs OCR, which is not enabled.</div> : sections.slice(page * 20, (page + 1) * 20).map(section => <article className={`preview-passage ${target?.sectionId === section.section_id ? 'target' : ''}`} id={`passage-${section.section_id}`} tabIndex={-1} key={section.section_id}><h3>{locationLabel(result.extraction.sources?.[section.section_id], section)}</h3><p>{textPieces(texts[section.section_id] ?? section.text, ranges[section.section_id] || []).map((piece, i) => piece.ids.length ? <span key={i} role="button" tabIndex={0} className={`evidence-highlight severity-${highlightSeverity(piece.ids, findings, fixed)} ${piece.ids.includes(selected) ? 'selected' : ''}`} title={`${piece.ids.length} concern${piece.ids.length > 1 ? 's' : ''}; select to review`} onClick={() => onSelect(piece.ids)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(piece.ids); } }}>{piece.text}</span> : <span key={i}>{piece.text}</span>)}</p></article>)}</div>{count > 1 && <div className="pagination"><button className="outline" disabled={!page} onClick={() => setPage(p => p - 1)}>Previous</button><span>Passages {page * 20 + 1}–{Math.min((page + 1) * 20, sections.length)} of {sections.length}</span><button className="outline" disabled={page >= count - 1} onClick={() => setPage(p => p + 1)}>Next</button></div>}</section>;
}
