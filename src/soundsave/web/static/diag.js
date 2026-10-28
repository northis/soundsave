/* Playback diagnostics: a small ring buffer of events kept in localStorage,
   so what happened while the app was in the background can be read later. */

const KEY = 'soundsave:diag';
const LIMIT = 400;

let entries = [];
try { entries = JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (error) { entries = []; }
if (!Array.isArray(entries)) entries = [];

let saveTimer = null;
function save() {
  if (saveTimer) return;
  saveTimer = setTimeout(() => {
    saveTimer = null;
    try { localStorage.setItem(KEY, JSON.stringify(entries)); } catch (error) { /* quota */ }
  }, 300);
}

export function diag(event, data = {}) {
  entries.push({ t: new Date().toISOString(), e: event, ...data });
  if (entries.length > LIMIT) entries = entries.slice(-LIMIT);
  save();
}

export function diagText() {
  const header = `Soundsave diag · ${navigator.userAgent}`;
  return [header, ...entries.map(({ t, e, ...rest }) => {
    const extra = Object.entries(rest).map(([k, v]) => `${k}=${v}`).join(' ');
    return `${t.slice(11, 23)} ${e}${extra ? ` ${extra}` : ''}`;
  })].join('\n');
}

export function diagClear() {
  entries = [];
  try { localStorage.removeItem(KEY); } catch (error) { /* ignore */ }
}

function srcKind(audio) {
  const src = audio.currentSrc || audio.src || '';
  if (!src) return 'none';
  if (src.startsWith('blob:')) return 'blob';
  if (src.includes('stream=1')) return 'stream';
  return 'sw';
}

export function audioSnapshot(audio) {
  let buffered = 0;
  try {
    for (let i = 0; i < audio.buffered.length; i += 1) {
      if (audio.buffered.start(i) <= audio.currentTime + 0.5) buffered = audio.buffered.end(i);
    }
  } catch (error) { /* ignore */ }
  return {
    src: srcKind(audio),
    pos: audio.currentTime.toFixed(1),
    buf: buffered.toFixed(1),
    rs: audio.readyState,
    ns: audio.networkState,
    paused: audio.paused,
    vis: document.visibilityState,
    err: audio.error ? audio.error.code : '',
  };
}

export function watchAudio(audio) {
  const events = ['play', 'playing', 'pause', 'waiting', 'stalled', 'suspend', 'emptied',
    'abort', 'error', 'ended', 'loadstart', 'canplay', 'seeking'];
  for (const name of events) {
    audio.addEventListener(name, () => diag(`audio:${name}`, audioSnapshot(audio)));
  }
  for (const name of ['visibilitychange', 'freeze', 'resume']) {
    document.addEventListener(name, () => diag(`page:${name}`, audioSnapshot(audio)));
  }
  for (const name of ['online', 'offline', 'pagehide', 'pageshow']) {
    window.addEventListener(name, () => diag(`page:${name}`, audioSnapshot(audio)));
  }
  // A heartbeat while playing: gaps between beats show when timers were frozen.
  setInterval(() => {
    if (!audio.paused) diag('beat', audioSnapshot(audio));
  }, 15000);
}
