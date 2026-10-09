import { useEffect, useMemo, useState } from 'react';
import { getJob, getUpload, getResults, cancelJob, startAnalysis, originalUrl } from '../services/api';
import { categories, mappedEvidence, humanise } from '../utils/formatters';
import Icon from '../components/Icon';
import DocumentPreview from '../components/DocumentPreview';
import ConcernCard from '../components/ConcernCard';

export default function ResultsPage({ jobId }) {
  const [job, setJob] = useState(null); const [upload, setUpload] = useState(null); const [response, setResponse] = useState(null); const [error, setError] = useState(''); const [retry, setRetry] = useState(0); const [busy, setBusy] = useState(false); const [selected, setSelected] = useState(null); const [target, setTarget] = useState(null); const [severity, setSeverity] = useState('All'); const [category, setCategory] = useState('All'); const [manifest, setManifest] = useState(null);
  useEffect(() => {
    const controller = new AbortController(); let timer; let lastSnapshot; let loadedUpload = false;
    async function poll() {
      try {
        const next = await getJob(jobId, controller.signal); if (controller.signal.aborted) return; setJob(next); setError('');
        if (!loadedUpload) { const item = await getUpload(next.upload_id, controller.signal); setUpload(item); loadedUpload = true; }
        if (next.result_available && next.updated_at !== lastSnapshot) { const data = await getResults(jobId, controller.signal); if (controller.signal.aborted) return; setResponse(data); lastSnapshot = next.updated_at; }
        if (!next.is_final && !controller.signal.aborted) timer = setTimeout(poll, 1000);
      } catch (e) { if (!controller.signal.aborted) setError(e.message); }
    }
    poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [jobId, retry]);
  const result = response?.result || (upload ? { extraction: upload.extraction } : null);
  const local = result?.analysis?.findings || [];
  const all = local;
  // Rendered page of each finding's evidence, keyed by finding and source section.
  const pages = useMemo(() => { const map = new Map(); for (const h of manifest?.highlights || []) { const key = `${h.finding_id}|${h.section_id}`; if (!map.has(key) || h.page < map.get(key)) map.set(key, h.page); } return map; }, [manifest]);
  const visible = f => (severity === 'All' || (f.severity || 'Unassessed') === severity) && (category === 'All' || f.category === category);
  function select(id, evidence = null) { setSelected(id); setCategory('All'); setSeverity('All'); const finding = all.find(f => f.finding_id === id); const mapped = finding && mappedEvidence(finding, result); if (evidence || mapped?.length) setTarget({ ...(evidence || mapped[0]), revision: Date.now() }); }
  function fromHighlight(ids) { setSelected(ids[0]); setTarget(null); setCategory('All'); setSeverity('All'); setTimeout(() => { document.getElementById(`finding-${ids[0]}`)?.focus(); }, 20); }
  async function cancel() { setBusy(true); try { setJob(await cancelJob(jobId)); setRetry(r => r + 1); } catch (e) { setError(e.message); } finally { setBusy(false); } }
  async function rerun() { setBusy(true); try { const next = await startAnalysis(job.upload_id); location.hash = `/results/${next.job_id}`; } catch (e) { setError(e.message); } finally { setBusy(false); } }
  function downloadReport() { const blob = new Blob([JSON.stringify({ ...response, job }, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `CARE-report-${jobId}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  function feedback(saved) { setResponse(old => ({ ...old, feedback: [...(old.feedback || []).filter(f => f.finding_id !== saved.finding_id), saved] })); }
  const incomplete = job && job.status !== 'completed';
  return <div className="results page-width"><div className="results-heading"><div><p className="eyebrow">Your content review</p><h1>Analysis Results</h1><p className="filename">{!upload ? 'Loading your analysis…' : upload.extraction?.format === 'text' ? 'Text' : upload.filename}</p>{job && <p className="muted small">Started {new Date(job.created_at).toLocaleString()} · <strong>{humanise(job.status)}</strong></p>}</div><div className="result-actions">{response && <button className="outline" onClick={downloadReport}><Icon name="download" size={18} />Download Report <span className="small">JSON</span></button>}{upload && <a className="text-button" href={originalUrl(job.upload_id)}>Download original</a>}</div></div>
    {error && <div className="notice error" role="alert">{error} <button className="outline" onClick={() => setRetry(v => v + 1)}>Retry connection</button></div>}
    {!job && !error && <p role="status"><span className="spinner" />Loading analysis…</p>}
    {job && !job.is_final && <section className="progress-panel" aria-label="Analysis progress"><div><strong role="status">{job.status === 'queued' ? 'Waiting in the local analysis queue' : job.status === 'cancelling' ? 'Cancelling analysis…' : 'Analysing your content'}</strong><span>{Math.round(job.progress || 0)}%</span></div><progress value={job.progress} max="100" aria-label="Analysis progress" /><p className="small muted">You can leave this page and return using its link. Completed passages will appear as they become available.</p><button className="outline" onClick={cancel} disabled={busy || job.cancel_requested}>Cancel analysis</button></section>}
    {incomplete && <div className="notice warning" role="status"><Icon name="alert" /><div><strong>{job.is_final ? 'This is not a complete assessment.' : 'Analysis is still in progress.'}</strong><p>{job.error?.message || 'Only successfully processed content is included. Missing findings do not mean the remaining content is free of concerns.'}</p></div></div>}
    {job?.status === 'no_text' && <div className="notice warning">No selectable text was found. Image and OCR analysis are not enabled.</div>}
    {job?.is_final && <button className="text-button rerun" onClick={rerun} disabled={busy}>Run a new analysis of this upload</button>}
    <div className="summary-grid">{[['file', 'Passage concerns', local.length, 'blue'], ['alert', 'High severity', local.filter(f => f.severity === 'High').length, 'pink'], ['alert', 'Medium severity', local.filter(f => f.severity === 'Medium').length, 'yellow'], ['check', 'Low severity', local.filter(f => f.severity === 'Low').length, 'green']].map(([icon, title, count, color]) => <div className={`summary-tile ${color}`} key={title}><span className="summary-icon"><Icon name={icon} size={27} /></span><div><span>{title}</span><strong>{response ? count : '—'}</strong></div></div>)}</div>
    {!!local.filter(f => !f.severity).length && <p className="small muted">{local.filter(f => !f.severity).length} passage concerns have unassessed severity.</p>}
    <div className="results-grid"><DocumentPreview jobId={jobId} result={result} findings={all} selected={selected} target={target} onSelect={fromHighlight} onManifest={setManifest} /><div className="results-right"><section className="panel"><div className="panel-heading wrap"><h2>Detected Concerns</h2><div className="filters"><label className="sr-only" htmlFor="severity-filter">Filter severity</label><select id="severity-filter" value={severity} onChange={e => setSeverity(e.target.value)}>{['All', 'High', 'Medium', 'Low', 'Unassessed'].map(s => <option key={s} value={s}>{s === 'All' ? 'All severities' : s}</option>)}</select><label className="sr-only" htmlFor="category-filter">Filter category</label><select id="category-filter" value={category} onChange={e => setCategory(e.target.value)}><option value="All">All categories</option>{categories.map(c => <option key={c}>{c}</option>)}</select></div></div>
    <div className="concern-list">{!local.filter(visible).length && <p className="empty-state">{!response ? 'Findings will appear when a window finishes.' : local.length ? 'No passage concerns match these filters.' : job?.status === 'completed' ? 'No passage concerns were flagged in the processed text. This does not guarantee that every issue was detected.' : 'No passage findings are available in this assessment.'}</p>}
    {local.filter(visible).map(f => <ConcernCard key={f.finding_id} finding={f} result={result} job={job} selected={selected === f.finding_id} onSelect={() => selected === f.finding_id ? setSelected(null) : select(f.finding_id)} onEvidence={e => select(f.finding_id, e)} feedback={response?.feedback?.find(x => x.finding_id === f.finding_id)} onFeedback={feedback} pages={pages} />)}</div></section>
    <section className="panel category-panel"><h2>Concerns by Category</h2><p className="small muted">Passage findings only; overlapping issues may remain for review.</p>{categories.map(c => { const n = local.filter(f => f.category === c).length; return <div className="category-row" key={c}><span>{c}</span><span className="bar-track"><span style={{ width: `${local.length ? n / local.length * 100 : 0}%` }} /></span><strong>{n}</strong></div>; })}</section>
    </div></div>
    <p className="disclaimer">Potential concerns only. Educators make the final decision.</p></div>;
}
