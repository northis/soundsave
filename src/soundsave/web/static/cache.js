/* Local audio cache: Cache API for bodies, IndexedDB for the LRU index. */

const AUDIO_CACHE = 'soundsave-audio-v1';
const DB_NAME = 'soundsave-cache';
const STORE = 'files';

function openDb() {
  return new Promise((resolve, reject) => {
    if (!('indexedDB' in window)) {
      reject(new Error('IndexedDB is unavailable'));
      return;
    }
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE)) {
        request.result.createObjectStore(STORE, { keyPath: 'url' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('IndexedDB error'));
  });
}

async function idbAll() {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly');
    const request = tx.objectStore(STORE).getAll();
    request.onsuccess = () => resolve(request.result || []);
    request.onerror = () => reject(request.error || new Error('IndexedDB read error'));
  });
}

async function idbPut(value) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).put(value);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error('IndexedDB write error'));
  });
}

async function idbDelete(url) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).delete(url);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error('IndexedDB delete error'));
  });
}

async function idbClear() {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error('IndexedDB clear error'));
  });
}

export function formatBytes(bytes) {
  if (!bytes || bytes < 0) return '0 МБ';
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} ГБ`;
}

export class CacheManager {
  constructor({ onChange, onProgress } = {}) {
    this.available = 'caches' in window;
    this.onChange = onChange || (() => {});
    this.onProgress = onProgress || (() => {});
    this.entries = new Map();
    this.active = null;
    this.autoCache = localStorage.getItem('soundsave:autoCache') !== '0';
    this.limitBytes = Number(localStorage.getItem('soundsave:cacheLimitMb') || 2048) * 1024 * 1024;
  }

  async init() {
    if (!this.available) return;
    try {
      const stored = await idbAll();
      this.entries = new Map(stored.map((entry) => [entry.url, entry]));
      await this._reconcile();
    } catch (error) {
      console.warn('Cache index is unavailable', error);
    }
    this._notify();
  }

  state(track) {
    if (!track || !track.audio_url) return { status: 'none', progress: 0 };
    const entry = this.entries.get(track.audio_url);
    if (!entry) return { status: 'none', progress: 0 };
    return { status: entry.status || 'cached', progress: entry.progress || 0, size: entry.size || 0 };
  }

  isCached(track) {
    return this.state(track).status === 'cached';
  }

  setAuto(value) {
    this.autoCache = Boolean(value);
    localStorage.setItem('soundsave:autoCache', this.autoCache ? '1' : '0');
  }

  setLimitMb(value) {
    this.limitBytes = Math.max(0, Number(value) || 0) * 1024 * 1024;
    localStorage.setItem('soundsave:cacheLimitMb', String(value));
    this._enforce().catch((error) => console.warn('Cache limit enforcement failed', error));
  }

  async usage() {
    let total = 0;
    for (const entry of this.entries.values()) total += Number(entry.size || 0);
    return total;
  }

  async requestPersist() {
    try {
      if (navigator.storage && navigator.storage.persist) await navigator.storage.persist();
    } catch (error) {
      console.warn('Persistent storage was not granted', error);
    }
  }

  async download(track) {
    if (!this.available || !track || !track.audio_url) return false;
    if (this.isCached(track)) return true;
    if (this.active) this.active.abort();
    const controller = new AbortController();
    this.active = controller;
    const url = track.audio_url;
    const separator = url.includes('?') ? '&' : '?';
    this.entries.set(url, { url, status: 'caching', progress: 0, size: 0, lastUsed: Date.now() });
    this.onProgress(track, 0);
    this._notify();
    try {
      const response = await fetch(`${url}${separator}download=1`, {
        credentials: 'same-origin',
        cache: 'no-store',
        signal: controller.signal,
      });
      const type = response.headers.get('content-type') || '';
      if (!response.ok || !type.startsWith('audio/')) {
        throw new Error(`HTTP ${response.status}: не удалось скачать трек`);
      }
      const total = Number(response.headers.get('content-length') || 0);
      const cache = await caches.open(AUDIO_CACHE);
      const clone = response.clone();
      const headers = new Headers(clone.headers);
      headers.delete('content-disposition');
      let putError = null;
      const put = cache.put(
        new Request(url),
        new Response(clone.body, { status: 200, headers }),
      ).catch((error) => {
        putError = error;
      });

      let received = 0;
      if (response.body) {
        const reader = response.body.getReader();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          received += value.byteLength;
          const progress = total ? Math.min(1, received / total) : 0;
          this.entries.set(url, {
            url, status: 'caching', progress, size: received, lastUsed: Date.now(),
          });
          this.onProgress(track, progress);
        }
      }
      await put;
      if (putError) throw putError;
      const size = total || received;
      this.entries.set(url, { url, status: 'cached', progress: 1, size, lastUsed: Date.now() });
      await idbPut(this.entries.get(url));
      await this._enforce();
      this.onProgress(track, 1);
      this._notify();
      return true;
    } catch (error) {
      if (error.name === 'AbortError') {
        this.entries.delete(url);
        this._notify();
        return false;
      }
      this.entries.set(url, { url, status: 'error', progress: 0, size: 0, lastUsed: Date.now() });
      this._notify();
      throw error;
    } finally {
      if (this.active === controller) this.active = null;
    }
  }

  async clear() {
    if (!this.available) return;
    if (this.active) this.active.abort();
    await caches.delete(AUDIO_CACHE);
    this.entries.clear();
    try { await idbClear(); } catch (error) { console.warn(error); }
    this._notify();
  }

  async _reconcile() {
    const cache = await caches.open(AUDIO_CACHE);
    const keys = await cache.keys();
    const urls = new Set(keys.map((request) => request.url));
    let changed = false;
    for (const url of [...this.entries.keys()]) {
      if (!urls.has(url)) {
        this.entries.delete(url);
        await idbDelete(url);
        changed = true;
      }
    }
    for (const url of urls) {
      if (!this.entries.has(url)) {
        this.entries.set(url, { url, status: 'cached', progress: 1, size: 0, lastUsed: 0 });
        await idbPut(this.entries.get(url));
        changed = true;
      }
    }
    if (changed) await this._enforce();
  }

  async _enforce() {
    if (!this.limitBytes) return;
    const cached = [...this.entries.values()]
      .filter((entry) => entry.status === 'cached')
      .sort((left, right) => (left.lastUsed || 0) - (right.lastUsed || 0));
    let total = cached.reduce((sum, entry) => sum + Number(entry.size || 0), 0);
    if (total <= this.limitBytes) return;
    const cache = await caches.open(AUDIO_CACHE);
    for (const entry of cached) {
      if (total <= this.limitBytes) break;
      await cache.delete(new Request(entry.url));
      this.entries.delete(entry.url);
      await idbDelete(entry.url);
      total -= Number(entry.size || 0);
    }
    this._notify();
  }

  _notify() {
    this.onChange(this);
  }
}
