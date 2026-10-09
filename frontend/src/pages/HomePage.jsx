import { useEffect, useRef, useState } from 'react';
import Icon from '../components/Icon';
import { uploadText, uploadFile, startAnalysis } from '../services/api';

const extensions = ['pdf', 'docx', 'pptx', 'xlsx', 'xlsm', 'xls', 'txt', 'md', 'csv', 'tsv'];
const steps = [['file', 'Upload', 'Add a document or paste your teaching material.'], ['search', 'Analyse', 'Review content with the CARE inclusivity framework.'], ['bulb', 'Improve', 'Explore explanations and practical recommendations.'], ['chart', 'Support', 'Create a more inclusive learning environment.']];
const benefits = [['shield', 'Research-informed', 'Guided by gender inclusivity research.'], ['file', 'Practical feedback', 'Clear evidence, explanations and suggestions.'], ['clock', 'A focused review', 'Find potential concerns in one place.'], ['people', 'Educator-led', 'You make the final decision.']];

export default function HomePage() {
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [text, setText] = useState(''); const [file, setFile] = useState(null); const [error, setError] = useState(''); const [busy, setBusy] = useState(false); const [drag, setDrag] = useState(false); const [stage, setStage] = useState('');
  function choose(files) {
    setError('');
    if (files.length !== 1) { setError('Choose one document at a time.'); return; }
    const selected = files[0];
    if (!extensions.includes(selected.name.split('.').pop().toLowerCase())) { setFile(null); setError('Choose a supported text document. Image uploads and scanned text require the later OCR stage.'); return; }
    if (selected.size > 20_000_000) { setFile(null); setError('The file exceeds the 20 MB limit.'); return; }
    setFile(selected);
  }
  async function analyse(mode) {
    setBusy(true); setError(''); setStage('Preparing your content…');
    try {
      const upload = mode === 'file' ? await uploadFile(file) : await uploadText(text);
      setStage('Adding your analysis to the queue…');
      const job = await startAnalysis(upload.upload_id);
      if (mounted.current) location.hash = `/results/${job.job_id}`;
    } catch (e) { setError(e.message); } finally { setBusy(false); setStage(''); }
  }
  return <div className="home page-width"><section className="hero"><p className="eyebrow">Fairer content. Brighter learning.</p><h1>Create More Inclusive<br className="desktop-break" /> Teaching Materials</h1><p className="hero-description">Upload your teaching materials and let CARE analyse them for potential gender inclusivity concerns, with clear explanations and practical recommendations.</p><div className="steps">{steps.map(([icon, title, copy]) => <div className="step" key={title}><span className="icon-circle"><Icon name={icon} size={34} /></span><h2>{title}</h2><p>{copy}</p></div>)}</div></section>
    <section className="input-panel" aria-label="Start a new analysis"><div className={`drop-zone ${drag ? 'dragging' : ''}`} onDragOver={e => { e.preventDefault(); if (!busy) setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={e => { e.preventDefault(); setDrag(false); if (!busy) choose(e.dataTransfer.files); }}><Icon name="upload" size={48} /><h2>Drag and drop your file here</h2><p className="or">or</p><label className={`button choose-file ${busy ? 'disabled' : ''}`}>Choose File<input type="file" aria-label="Choose a document" accept={extensions.map(e => `.${e}`).join(',')} disabled={busy} onChange={e => { if (e.target.files.length) choose(e.target.files); e.target.value = ''; }} /></label><p className="format-hint">PDF, PPTX, DOCX, Excel, TXT, Markdown, CSV or TSV<br />20 MB maximum · up to 100 pages</p>{file && <div className="selected-file"><Icon name="file" /><span>{file.name}<small>{(file.size / 1_000_000).toFixed(2)} MB</small></span><button className="text-button" disabled={busy} onClick={() => setFile(null)} aria-label="Remove selected file">Remove</button></div>}{file && <button className="button wide" disabled={busy} onClick={() => analyse('file')}>Analyse Document</button>}</div>
    <div className="text-entry"><h2>Or Enter Text Directly</h2><label htmlFor="teaching-text">Paste or type your teaching material below to analyse it for potential gender inclusivity concerns.</label><textarea id="teaching-text" placeholder="Type or paste your text here…" value={text} onChange={e => setText(e.target.value)} maxLength={300000} disabled={busy} /><p className="char-count">{Array.from(text).length.toLocaleString()} / 300,000 characters</p><button className="button wide" disabled={busy || !text.trim()} onClick={() => analyse('text')}>Analyse Text <Icon name="arrow" size={18} /></button></div></section>
    {error && <div className="notice error" role="alert">{error}</div>}{busy && <p role="status" className="notice"><span className="spinner" />{stage}</p>}
    
    <section className="benefits"><h2>Why Use CARE?</h2><div className="benefit-grid">{benefits.map(([icon, title, copy]) => <article key={title}><Icon name={icon} size={32} /><h3>{title}</h3><p>{copy}</p></article>)}</div></section></div>;
}
