const paths = {
  settings: <><path d="m9 3 .6-2h4.8l.6 2 2 .9 2-.5 2.4 4.2-1.4 1.5v2.2l1.4 1.5-2.4 4.2-2-.5-2 .9-.6 2H9l-.6-2-2-.9-2 .5L2 13.8l1.4-1.5v-2.2L2 8.6l2.4-4.2 2 .5z" transform="translate(0 1)" /><circle cx="12" cy="12" r="3" /></>,
  file: <><path d="M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6" /></>,
  upload: <><path d="M7 17H5a4 4 0 0 1-1-8 7 7 0 0 1 14-1 4.5 4.5 0 0 1 0 9h-1M12 21V10m-4 4 4-4 4 4" /></>,
  search: <><circle cx="10" cy="10" r="7" /><path d="m15 15 6 6" /></>,
  bulb: <><path d="M9 18h6m-6 3h6M8 14a6 6 0 1 1 8 0l-1 4H9zM12 1v1M2 9H0m24 0h-2M3 2l2 2m16-2-2 2" /></>,
  chart: <><path d="M3 21h19M5 20V12h4v8m3 0V7h4v13m3 0V3h3v17" /></>,
  shield: <><path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6zM7 11l4 4 6-7" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 6v7l4 2" /></>,
  people: <><circle cx="9" cy="7" r="3" /><path d="M2 21v-4a7 7 0 0 1 14 0v4M18 4a3 3 0 0 1 0 6m1 4a5 5 0 0 1 3 4v3" /></>,
  target: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><path d="m12 12 9-9m-4 0h4v4" /></>,
  check: <path d="m5 12 4 4L20 5" />,
  alert: <><circle cx="12" cy="12" r="9" /><path d="M12 6v7m0 4v.1" /></>,
  download: <path d="M12 2v13m-5-5 5 5 5-5M4 16v5h16v-5" />,
  arrow: <path d="m9 5 7 7-7 7" />,
};
export default function Icon({ name = 'file', size = 24 }) { return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.file}</svg>; }
