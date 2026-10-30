import { useEffect, useMemo, useRef, useState } from 'react';
import { getDocumentPreview, jobPreviewPageUrl } from '../services/api';

export function highlightSeverity(ids, findings, fixed) {
  // A passage whose every concern has an applied fix is shown as fixed.
  if (fixed && ids.length && ids.every(id => fixed.has(id))) return 'fixed';
  const levels = findings.filter(f => ids.includes(f.finding_id) && !fixed?.has(f.finding_id)).map(f => f.severity);
  return ['High', 'Medium', 'Low'].find(level => levels.includes(level))?.toLowerCase() || 'unassessed';
}

export function SeverityLegend() {
  return <div className="severity-legend" aria-label="Highlight colors">
    {['High', 'Medium', 'Low', 'Unassessed', 'Fixed'].map(level => <span key={level}><i className={`severity-${level.toLowerCase()}`} />{level}</span>)}
  </div>;
}

export default function RenderedPreview({ jobId, result, findings, fixed, revision, selected, target, onSelect, onTextView, onManifest }) {
  const preview = useRef(null);
  const scroller = useRef(null);
  const pageRefs = useRef({});
  const [manifest, setManifest] = useState(null);
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(100);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [imageErrors, setImageErrors] = useState({});
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    getDocumentPreview(jobId, controller.signal).then(data => {
      if (!controller.signal.aborted) { setManifest(data); onManifest?.(data); setLoading(false); }
    }).catch(e => { if (!controller.signal.aborted) { setError(e.message); setLoading(false); } });
    return () => controller.abort();
  }, [jobId, result, retry, revision]);
  useEffect(() => { setImageErrors({}); }, [retry]);
  // All pages are stacked in one scroll area; page controls jump within it.
  function scrollToPage(number) {
    const box = scroller.current, element = pageRefs.current[number];
    if (box && element) box.scrollTop = element.offsetTop - 12;
    setPage(number);
  }
  function trackPage() {
    const box = scroller.current;
    if (!box || !manifest) return;
    const line = box.scrollTop + box.clientHeight / 3;
    let visible = 1;
    for (const p of manifest.pages) if ((pageRefs.current[p.number]?.offsetTop ?? Infinity) <= line) visible = p.number;
    setPage(visible);
  }
  useEffect(() => { if (manifest) scrollToPage(page); }, [zoom]);
  useEffect(() => {
    if (!target || !manifest) return;
    const match = manifest.highlights.find(h => h.section_id === target.sectionId && h.finding_id === selected);
    if (!match) return;
    setPage(match.page);
    const timer = setTimeout(() => {
      const mark = pageRefs.current[match.page]?.querySelector('.document-highlight.selected');
      mark?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
      mark?.focus({ preventScroll: true });
    }, 50);
    return () => clearTimeout(timer);
  }, [target, manifest, selected]);
  // Only concerns that pass the current filters are highlighted.
  const shownIds = useMemo(() => new Set(findings.map(f => f.finding_id)), [findings]);
  const highlightsByPage = useMemo(() => {
    const pages = new Map();
    for (const mark of manifest?.highlights || []) {
      if (!shownIds.has(mark.finding_id)) continue;
      if (!pages.has(mark.page)) pages.set(mark.page, new Map());
      const groups = pages.get(mark.page), key = mark.rect.join(',');
      if (!groups.has(key)) groups.set(key, { rect: mark.rect, ids: [] });
      const ids = groups.get(key).ids;
      if (!ids.includes(mark.finding_id)) ids.push(mark.finding_id);
    }
    return new Map([...pages].map(([number, groups]) => [number, [...groups.values()]]));
  }, [manifest, shownIds]);
  const unmapped = (manifest?.unmapped_finding_ids || []).filter(id => shownIds.has(id));
  function selectAt(event, mark, highlights) {
    const bounds = event.currentTarget.parentElement.getBoundingClientRect();
    const x = event.detail ? (event.clientX - bounds.left) / bounds.width : mark.rect[0] + mark.rect[2] / 2;
    const y = event.detail ? (event.clientY - bounds.top) / bounds.height : mark.rect[1] + mark.rect[3] / 2;
    const ids = highlights.filter(h => x >= h.rect[0] && x <= h.rect[0] + h.rect[2] && y >= h.rect[1] && y <= h.rect[1] + h.rect[3]).flatMap(h => h.ids);
    onSelect([...new Set([...mark.ids, ...ids])]);
  }
  const failedPages = Object.keys(imageErrors);
  return <section ref={preview} className="panel native-preview" aria-labelledby="native-preview-title">
    <div className="panel-heading"><h2 id="native-preview-title">Preview of the Input</h2><span className="small-tag">Document</span></div>
    <SeverityLegend />
    <p className="small muted">Scroll through the document and select a highlight to review its concerns. Overlapping concerns use the highest severity color.</p>
    {loading && <p role="status">{manifest ? 'Updating the preview with your fixes…' : 'Preparing document preview…'}</p>}
    {error && <div className="notice warning" role="alert"><div>{error}<div><button className="text-button" onClick={() => setRetry(v => v + 1)}>Retry preview</button> · <button className="text-button" onClick={onTextView}>Use text view</button></div></div></div>}
    {manifest && <>
      <div className="document-toolbar">
        <button className="outline" disabled={page <= 1} onClick={() => scrollToPage(page - 1)}>Previous</button>
        <label>Page <select aria-label="Document page" value={page} onChange={e => scrollToPage(Number(e.target.value))}>{manifest.pages.map(p => <option key={p.number} value={p.number}>{p.number}</option>)}</select> of {manifest.pages.length}</label>
        <button className="outline" disabled={page >= manifest.pages.length} onClick={() => scrollToPage(page + 1)}>Next</button>
        <label>Zoom <select aria-label="Document zoom" value={zoom} onChange={e => setZoom(Number(e.target.value))}>{[100, 150, 200].map(z => <option key={z} value={z}>{z === 100 ? 'Fit width' : `${z}%`}</option>)}</select></label>
      </div>
      <div className="native-document-scroll" ref={scroller} onScroll={trackPage}>
        {manifest.pages.map(current => {
          const highlights = highlightsByPage.get(current.number) || [];
          return <div key={current.number} ref={el => { pageRefs.current[current.number] = el; }} className="rendered-page" data-page={current.number} style={{ width: `${zoom}%`, aspectRatio: `${current.width}/${current.height}` }}>
            <img loading="lazy" src={jobPreviewPageUrl(jobId, current.number) + `?v=${manifest.revision || 'original'}&retry=${retry}`} alt={`Document page ${current.number}. Use text view for accessible document text.`} onError={() => setImageErrors(e => ({ ...e, [current.number]: true }))} />
            {!imageErrors[current.number] && highlights.map((mark, i) => <button key={i}
              className={`document-highlight severity-${highlightSeverity(mark.ids, findings, fixed)} ${mark.ids.includes(selected) ? 'selected' : ''}`}
              style={{ left: `${mark.rect[0] * 100}%`, top: `${mark.rect[1] * 100}%`, width: `${mark.rect[2] * 100}%`, height: `${mark.rect[3] * 100}%` }}
              aria-label={mark.ids.map(id => { const f = findings.find(f => f.finding_id === id); return `${fixed?.has(id) ? 'Fixed' : `${f?.severity || 'Unassessed'} severity`}: ${f?.category || 'concern'}`; }).join('; ')}
              title="Select to review this highlighted concern" onClick={e => selectAt(e, mark, highlights)} />)}
          </div>;
        })}
      </div>
      {!!failedPages.length && <p role="alert">Page{failedPages.length > 1 ? 's' : ''} {failedPages.join(', ')} could not be loaded. <button className="text-button" onClick={() => setRetry(v => v + 1)}>Retry preview</button></p>}
      {manifest.converted && <p className="small muted">Rendered locally from the original Office file. Fonts and pagination can differ from Microsoft Office.</p>}
      {!!unmapped.length && <p className="notice warning small">{unmapped.length} concern(s) could not be placed reliably in this layout. <button className="text-button" onClick={onTextView}>Review in text view</button></p>}
      {target && !manifest.highlights.some(h => h.section_id === target.sectionId && h.finding_id === selected) && <p className="small">This source is not mapped to a rendered page. <button className="text-button" onClick={onTextView}>Show its text location</button></p>}
    </>}
  </section>;
}
