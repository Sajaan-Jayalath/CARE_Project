import { useEffect, useState } from 'react';
import Navbar from './components/Navbar';
import Footer from './components/Footer';
import HomePage from './pages/HomePage';
import AboutPage from './pages/AboutPage';
import ResultsPage from './pages/ResultsPage';

export default function App() {
  const [route, setRoute] = useState(location.hash || '#/');
  useEffect(() => { const change = () => { setRoute(location.hash || '#/'); }; window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change); }, []);
  const jobId = route.match(/^#\/results\/([a-f0-9]{32})$/)?.[1];
  const page = jobId ? 'results' : route === '#/about' ? 'about' : 'home';
  useEffect(() => {
    document.title = `${page === 'results' ? 'Analysis Results' : page === 'about' ? 'About' : 'Inclusive Teaching'} | CARE`;
    document.querySelector('main')?.focus({ preventScroll: true });
    const newAnalysis = page === 'home' && new URLSearchParams(route.split('?')[1] || '').has('new');
    const inputs = newAnalysis && document.querySelector('.input-panel');
    if (inputs) {
      // Center the form on desktop; show its beginning when it exceeds the viewport.
      inputs.scrollIntoView({ block: inputs.offsetHeight <= window.innerHeight ? 'center' : 'start', behavior: 'instant' });
    } else {
      window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
    }
  }, [route, page]);
  return <><a className="skip-link" href="#main" onClick={e => { e.preventDefault(); document.querySelector('main')?.focus(); }}>Skip to content</a><Navbar page={page} /><main id="main" tabIndex={-1}>{page === 'results' ? <ResultsPage key={jobId} jobId={jobId} /> : page === 'about' ? <AboutPage /> : <HomePage key={route} />}</main><Footer /></>;
}
