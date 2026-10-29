/* Soundsave service worker: app shell, API fallback, covers and audio cache. */

const SHELL_CACHE = 'soundsave-shell-v13';
const AUDIO_CACHE = 'soundsave-audio-v1';
const COVER_CACHE = 'soundsave-covers-v1';
const API_CACHE = 'soundsave-api-v1';
const KEEP = new Set([SHELL_CACHE, AUDIO_CACHE, COVER_CACHE, API_CACHE]);

const PRECACHE = [
  '/',
  '/static/style.css?v=2.2.1',
  '/static/app.js?v=2.2.1',
  '/static/player.js?v=2.2.1',
  '/static/cache.js?v=2.2.1',
  '/static/diag.js?v=2.2.1',
  '/favicon.svg',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/maskable-512.png',
  '/static/icons/apple-touch-icon-180.png',
  '/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    await cache.addAll(PRECACHE);
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((name) => !KEEP.has(name)).map((name) => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});

function parseRange(header, size) {
  if (!header || !header.toLowerCase().startsWith('bytes=')) return null;
  const spec = header.slice(6).trim();
  if (!spec || spec.includes(',')) return null;
  const [startRaw, endRaw] = spec.split('-');
  let start;
  let end;
  if (!startRaw) {
    const suffix = Number(endRaw);
    if (!suffix || size <= 0) return null;
    start = Math.max(0, size - suffix);
    end = size - 1;
  } else {
    start = Number(startRaw);
    end = endRaw ? Number(endRaw) : size - 1;
  }
  if (!Number.isFinite(start) || !Number.isFinite(end) || size <= 0 || start < 0 || start >= size || end < start) {
    return null;
  }
  return [start, Math.min(end, size - 1)];
}

async function cachedAudio(request) {
  const cache = await caches.open(AUDIO_CACHE);
  const cached = await cache.match(request, { ignoreVary: true });
  if (!cached) return null;
  const range = request.headers.get('range');
  if (!range) return cached;
  const blob = await cached.blob();
  const parsed = parseRange(range, blob.size);
  if (!parsed) {
    return new Response(null, {
      status: 416,
      headers: { 'Content-Range': `bytes */${blob.size}`, 'Content-Length': '0' },
    });
  }
  const [start, end] = parsed;
  const headers = new Headers(cached.headers);
  headers.set('Content-Range', `bytes ${start}-${end}/${blob.size}`);
  headers.set('Content-Length', String(end - start + 1));
  headers.set('Accept-Ranges', 'bytes');
  return new Response(blob.slice(start, end + 1), { status: 206, headers });
}

async function audioResponse(request) {
  const cached = await cachedAudio(request);
  if (cached) return cached;
  try {
    return await fetch(request);
  } catch (error) {
    return new Response(null, { status: 504, statusText: 'Offline' });
  }
}

async function coverResponse(request) {
  const cache = await caches.open(COVER_CACHE);
  const cached = await cache.match(request, { ignoreVary: true });
  if (cached) return cached;
  try {
    const response = await fetch(request);
    const type = response.headers.get('content-type') || '';
    if (response.ok && type.startsWith('image/')) {
      try {
        await cache.put(request, response.clone());
      } catch (error) {
        /* storage full: still return the image */
      }
    }
    return response;
  } catch (error) {
    return new Response(null, { status: 504, statusText: 'Offline' });
  }
}

async function networkFirstApi(request) {
  try {
    const response = await fetch(request);
    const type = response.headers.get('content-type') || '';
    if (response.ok && type.includes('application/json')) {
      try {
        const cache = await caches.open(API_CACHE);
        await cache.put(request, response.clone());
      } catch (error) {
        /* storage full: still return the fresh response */
      }
    }
    return response;
  } catch (error) {
    const cached = await caches.match(request, { ignoreVary: true });
    if (cached) return cached;
    return new Response(JSON.stringify({ detail: 'Нет сети' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    });
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request, { ignoreVary: true });
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      try {
        const cache = await caches.open(SHELL_CACHE);
        await cache.put(request, response.clone());
      } catch (error) {
        /* storage full: still return the fresh response */
      }
    }
    return response;
  } catch (error) {
    return new Response(null, { status: 504, statusText: 'Offline' });
  }
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname === '/sw.js') return;

  if (url.pathname.startsWith('/api/')) {
    if (url.pathname.endsWith('/audio')) {
      if (url.searchParams.get('download') === '1') return;
      // Live playback must go straight to the network. A service worker can be
      // frozen together with the page on Android, which cuts the stream when
      // the buffer runs out. Cached tracks keep using the worker.
      if (url.searchParams.get('stream') === '1') return;
      event.respondWith(audioResponse(request));
      return;
    }
    if (url.pathname.endsWith('/cover')) {
      event.respondWith(coverResponse(request));
      return;
    }
    event.respondWith(networkFirstApi(request));
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(cacheFirst(new Request('/')).catch(async () => {
      const cached = await caches.match('/');
      return cached || new Response('Нет сети', { status: 503 });
    }));
    return;
  }

  if (url.pathname === '/manifest.webmanifest' || url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(request));
  }
});
