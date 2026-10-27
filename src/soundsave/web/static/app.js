import { Player } from './player.js?v=2.1.3';
import { CacheManager, formatBytes } from './cache.js?v=2.1.3';

const $ = (id) => document.getElementById(id);
const collator = new Intl.Collator('ru', { numeric: true, sensitivity: 'base' });
const MOBILE_QUERY = window.matchMedia('(max-width: 760px)');

const state = {
  playlists: [],
  playlistId: null,
  tracks: [],
  sorted: [],
  currentId: null,
  sort: localStorage.getItem('soundsave:sort') || 'number',
  sortDir: localStorage.getItem('soundsave:sortDir') || 'asc',
  userScrolled: false,
  programmaticScroll: false,
  loading: false,
  draggingSeek: false,
  draggingVolume: false,
  syncTimer: null,
  syncHidden: false,
  installPrompt: null,
  lastPositionSave: 0,
  wasPlayingBeforeHidden: false,
};

let player = null;
let cacheManager = null;

function icon(name, className = 'icon') {
  return `<svg class="${className}" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatTime(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return '0:00';
  const total = Math.floor(value);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}` : `${minutes}:${String(secs).padStart(2, '0')}`;
}

function formatSampleRate(hz) {
  if (!hz) return '—';
  const value = hz / 1000;
  return `${Number.isInteger(value) ? value : value.toFixed(1)} kHz`;
}

function formatBitrate(kbps) {
  return kbps ? `${kbps} kbps` : '—';
}

function toast(message, { error = false, timeout = 4500 } = {}) {
  const node = el('div', `toast${error ? ' error' : ''}`, message);
  $('toasts').appendChild(node);
  setTimeout(() => node.remove(), timeout);
}

async function api(path, { method = 'GET', body } = {}) {
  const headers = {};
  if (method !== 'GET') headers['X-Soundsave-Request'] = '1';
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
  });
  let data = null;
  try { data = await response.json(); } catch (error) { /* empty body */ }
  if (!response.ok) {
    const message = data && data.detail ? data.detail : `HTTP ${response.status}`;
    const failure = new Error(message);
    failure.status = response.status;
    throw failure;
  }
  return data;
}

/* ----------------------------- theme ----------------------------- */

function themePreference() {
  return localStorage.getItem('soundsave:theme') || 'system';
}

function resolvedTheme(preference = themePreference()) {
  if (preference === 'light' || preference === 'dark') return preference;
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(preference) {
  const resolved = resolvedTheme(preference);
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themePreference = preference;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = resolved === 'dark' ? '#18181b' : '#f4f4f5';
  const select = $('theme-select');
  if (select) select.value = preference;
  $('theme-btn').title = `Тема: ${preference === 'system' ? 'системная' : preference === 'dark' ? 'тёмная' : 'светлая'}`;
}

function setTheme(preference) {
  localStorage.setItem('soundsave:theme', preference);
  applyTheme(preference);
}

function cycleTheme() {
  const order = ['system', 'light', 'dark'];
  const current = themePreference();
  setTheme(order[(order.indexOf(current) + 1) % order.length]);
}

/* ----------------------------- playlist ----------------------------- */

function savedPlaylistId() {
  const value = Number(localStorage.getItem('soundsave:playlist'));
  return Number.isFinite(value) ? value : null;
}

function updateUrl() {
  const params = new URLSearchParams();
  if (state.playlistId) params.set('playlist', String(state.playlistId));
  if (state.sort !== 'number') params.set('sort', state.sort);
  if (state.sortDir !== 'asc') params.set('dir', state.sortDir);
  if (state.currentId) params.set('track', String(state.currentId));
  const query = params.toString();
  history.replaceState(null, '', query ? `?${query}` : location.pathname);
}

function renderPlaylistSelect() {
  const select = $('playlist-select');
  select.replaceChildren();
  for (const playlist of state.playlists) {
    const option = document.createElement('option');
    option.value = String(playlist.id);
    option.textContent = `${playlist.name}${playlist.readonly ? ' · только чтение' : ''} (${playlist.track_count})`;
    select.appendChild(option);
  }
  if (state.playlistId) select.value = String(state.playlistId);
}

function currentPlaylist() {
  return state.playlists.find((playlist) => playlist.id === state.playlistId) || null;
}

function updateSyncButton() {
  const playlist = currentPlaylist();
  const button = $('sync-btn');
  const visible = Boolean(playlist && playlist.syncable);
  button.hidden = !visible;
  if (!visible) return;
  const available = Boolean(playlist.sync_available);
  button.disabled = !available || Boolean(state.syncTimer);
  button.title = available ? 'Скачать новые треки из Яндекса' : 'Задайте YANDEX_TOKEN и PLAYLIST_URL в .env';
  button.querySelector('.sync-label').textContent = available ? 'Синхронизировать' : 'Sync недоступен';
}

async function loadPlaylists({ preserveSelection = false } = {}) {
  const data = await api('/api/playlists');
  state.playlists = data.playlists || [];
  if (!state.playlists.length) {
    state.playlistId = null;
    state.tracks = [];
    state.sorted = [];
    state.currentId = null;
    if (player) {
      player.pause();
      player.track = null;
      player.audio.removeAttribute('src');
      player.audio.load();
      player.setQueue([]);
    }
    updatePlayerTrack(null);
    renderPlaylistSelect();
    renderList();
    updateSyncButton();
    showEmpty('Нет плейлистов', 'Добавьте подпапки-плейлисты в MUSIC_DIR и обновите библиотеку.');
    return;
  }
  const params = new URLSearchParams(location.search);
  const fromUrl = Number(params.get('playlist'));
  const wanted = preserveSelection
    ? state.playlistId
    : (Number.isFinite(fromUrl) && fromUrl ? fromUrl : savedPlaylistId());
  const exists = state.playlists.some((playlist) => playlist.id === wanted);
  const fallback = state.playlists.find((playlist) => playlist.syncable) || state.playlists[0];
  const selected = exists ? wanted : fallback.id;
  if (preserveSelection && selected === state.playlistId) {
    renderPlaylistSelect();
    updateSyncButton();
    await loadTracks(selected, { preserveCurrent: true });
    return;
  }
  await selectPlaylist(selected, { initial: true, preserveCurrent: preserveSelection });
}

async function selectPlaylist(playlistId, { initial = false, preserveCurrent = false } = {}) {
  const id = Number(playlistId);
  if (!id) return;
  if (!initial && id === state.playlistId) return;
  const previousId = state.playlistId;
  const previousTrack = state.currentId;
  const previousPosition = player ? player.currentTime : 0;
  if (previousId && previousTrack) {
    localStorage.setItem(`soundsave:current:${previousId}`, String(previousTrack));
    localStorage.setItem(`soundsave:position:${previousId}`, String(previousPosition));
  }
  if (player) {
    player.pause();
    player.track = null;
    player.audio.removeAttribute('src');
    player.audio.load();
    player.setQueue([]);
  }
  state.playlistId = id;
  state.currentId = null;
  state.userScrolled = false;
  localStorage.setItem('soundsave:playlist', String(id));
  renderPlaylistSelect();
  updateSyncButton();
  await loadTracks(id, { restore: initial, preserveCurrent });
  updateUrl();
}

async function loadTracks(playlistId, { restore = false, preserveCurrent = false } = {}) {
  state.loading = true;
  showEmpty('Загрузка…', '');
  try {
    const data = await api(`/api/playlists/${playlistId}/tracks`);
    state.tracks = data.tracks || [];
    state.sorted = sortTracks(state.tracks);
    if (player) player.setQueue(state.sorted);
    if (restore) {
      const params = new URLSearchParams(location.search);
      const fromUrl = Number(params.get('track'));
      const saved = Number(localStorage.getItem(`soundsave:current:${playlistId}`));
      const wanted = Number.isFinite(fromUrl) && fromUrl ? fromUrl : saved;
      const track = state.sorted.find((item) => item.id === wanted);
      if (track) {
        const savedPosition = Number(localStorage.getItem(`soundsave:position:${playlistId}`) || 0);
        state.currentId = track.id;
        player.setTrack(track, { autoplay: false, startAt: savedPosition });
      }
    } else if (preserveCurrent && state.currentId) {
      const track = state.sorted.find((item) => item.id === state.currentId);
      if (track && player) player.track = track;
      if (!track) clearCurrent();
    }
    if (!state.currentId) updatePlayerTrack(null);
    renderList();
    if (state.currentId) centerCurrent(true);
    updateSyncButton();
    updateCacheUsage();
  } catch (error) {
    state.tracks = [];
    state.sorted = [];
    renderList();
    showEmpty('Не удалось загрузить плейлист', error.message);
    toast(error.message, { error: true });
  } finally {
    state.loading = false;
  }
}

function showEmpty(title, text) {
  const empty = $('empty-state');
  empty.hidden = false;
  $('empty-title').textContent = title;
  $('empty-text').textContent = text;
  if (state.tracks.length) empty.hidden = true;
}

/* ----------------------------- sorting / list ----------------------------- */

function sortTracks(tracks) {
  const direction = state.sortDir === 'desc' ? -1 : 1;
  const sorted = [...tracks];
  sorted.sort((left, right) => {
    let result = 0;
    if (state.sort === 'title') {
      result = collator.compare(left.title || '', right.title || '') || collator.compare(left.artist || '', right.artist || '');
    } else if (state.sort === 'artist') {
      result = collator.compare(left.artist || '', right.artist || '') || collator.compare(left.title || '', right.title || '');
    } else {
      result = (left.position || 0) - (right.position || 0);
    }
    return result * direction || left.id - right.id;
  });
  return sorted;
}

function applySort(sort, direction = state.sortDir) {
  state.sort = sort;
  state.sortDir = direction;
  localStorage.setItem('soundsave:sort', sort);
  localStorage.setItem('soundsave:sortDir', direction);
  document.querySelectorAll('#sort-control [data-sort]').forEach((button) => {
    button.classList.toggle('active', button.dataset.sort === sort);
  });
  $('sort-dir').textContent = direction === 'desc' ? '↓' : '↑';
  state.sorted = sortTracks(state.tracks);
  if (player) player.setQueue(state.sorted);
  state.userScrolled = false;
  renderList();
  centerCurrent(true);
  updateUrl();
}

function rowHeight() {
  const value = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--row-h'));
  return Number.isFinite(value) ? value : 52;
}

function renderList() {
  const canvas = $('list-canvas');
  const total = state.sorted.length;
  canvas.style.height = `${total * rowHeight()}px`;
  const empty = $('empty-state');
  if (!total) {
    canvas.replaceChildren();
    if (!state.loading) {
      const playlist = currentPlaylist();
      const title = playlist ? 'Плейлист пуст' : 'Нет плейлистов';
      const text = playlist && playlist.syncable
        ? 'Нажмите «Синхронизировать», чтобы скачать новые треки из Яндекса.'
        : 'Добавьте аудиофайлы в папку плейлиста и обновите библиотеку.';
      showEmpty(title, text);
    }
    return;
  }
  empty.hidden = true;
  renderVisible();
}

function renderVisible() {
  const canvas = $('list-canvas');
  const total = state.sorted.length;
  if (!total) return;
  const height = rowHeight();
  const viewport = $('list-scroll').clientHeight || 400;
  const scrollTop = $('list-scroll').scrollTop;
  const start = Math.max(0, Math.floor(scrollTop / height) - 8);
  const end = Math.min(total, Math.ceil((scrollTop + viewport) / height) + 8);
  const fragment = document.createDocumentFragment();
  for (let index = start; index < end; index += 1) {
    fragment.appendChild(buildRow(state.sorted[index], index, height));
  }
  canvas.replaceChildren(fragment);
  updateJumpButton();
}

function buildRow(track, index, height) {
  const row = el('div', 'track-row');
  row.style.top = `${index * height}px`;
  row.dataset.id = String(track.id);
  row.tabIndex = 0;
  row.setAttribute('role', 'option');
  row.setAttribute('aria-selected', String(track.id === state.currentId));
  row.setAttribute('aria-posinset', String(index + 1));
  row.setAttribute('aria-setsize', String(state.sorted.length));
  if (track.id === state.currentId) row.classList.add('is-current');

  row.appendChild(el('span', 'row-num', String(track.number)));

  const cover = el('div', 'row-cover');
  setCover(cover, track);
  row.appendChild(cover);

  const main = el('div', 'row-main');
  const title = el('div', 'row-title');
  if (track.id === state.currentId) {
    const eq = el('span', 'eq');
    eq.setAttribute('aria-hidden', 'true');
    eq.innerHTML = '<i></i><i></i><i></i>';
    title.appendChild(eq);
  }
  title.appendChild(document.createTextNode(track.title || 'Без названия'));
  main.appendChild(title);
  main.appendChild(el('div', 'row-artist', track.artist || 'Неизвестный исполнитель'));
  const meta = [
    `#${track.number}`,
    track.format,
    formatBitrate(track.bitrate_kbps),
    formatSampleRate(track.sample_rate_hz),
    formatTime(track.duration_s),
  ].filter(Boolean).join(' · ');
  main.appendChild(el('div', 'row-meta', meta));
  row.appendChild(main);

  row.appendChild(el('div', 'row-cell album', track.album || '—'));
  const formatCell = el('div', 'row-cell format');
  formatCell.appendChild(el('span', 'format-badge', track.format || '—'));
  row.appendChild(formatCell);
  row.appendChild(el('div', 'row-cell bitrate', formatBitrate(track.bitrate_kbps)));
  row.appendChild(el('div', 'row-cell sample', formatSampleRate(track.sample_rate_hz)));
  row.appendChild(el('div', 'row-cell time', formatTime(track.duration_s)));

  const cacheCell = el('div', 'cache-col');
  const cacheButton = el('button', 'cache-indicator');
  cacheButton.type = 'button';
  cacheButton.dataset.trackId = String(track.id);
  cacheButton.addEventListener('click', (event) => {
    event.stopPropagation();
    downloadTrack(track);
  });
  updateCacheIndicator(cacheButton, track);
  cacheCell.appendChild(cacheButton);
  row.appendChild(cacheCell);

  row.addEventListener('click', () => player.playTrack(track.id));
  row.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      event.stopPropagation();
      player.playTrack(track.id);
    }
  });
  return row;
}

function setCover(container, track) {
  container.replaceChildren();
  if (track && track.has_cover && track.cover_url) {
    const image = document.createElement('img');
    image.alt = '';
    image.loading = 'eager';
    image.decoding = 'async';
    image.src = track.cover_url;
    image.addEventListener('error', () => {
      container.replaceChildren();
      container.innerHTML = icon('note');
    });
    container.appendChild(image);
  } else {
    container.innerHTML = icon('note');
  }
}

function updateCacheIndicator(button, track) {
  const status = cacheManager ? cacheManager.state(track) : { status: 'none' };
  button.classList.remove('is-cached', 'is-caching', 'is-error');
  button.disabled = !cacheManager || !cacheManager.available;
  if (status.status === 'cached') {
    button.classList.add('is-cached');
    button.title = 'Скачано локально';
    button.innerHTML = icon('check');
  } else if (status.status === 'caching') {
    button.classList.add('is-caching');
    button.title = 'Загрузка в кэш';
    button.innerHTML = icon('download');
  } else if (status.status === 'error') {
    button.classList.add('is-error');
    button.title = 'Не удалось сохранить в кэш — нажмите, чтобы повторить';
    button.innerHTML = icon('refresh');
  } else {
    button.title = 'Скачать локально';
    button.innerHTML = icon('download');
  }
}

function scrollToTrack(trackId, { smooth = false } = {}) {
  const index = state.sorted.findIndex((track) => track.id === Number(trackId));
  if (index < 0) return;
  const height = rowHeight();
  const viewport = $('list-scroll').clientHeight || 400;
  const top = Math.max(0, index * height - (viewport - height) / 2);
  state.programmaticScroll = true;
  $('list-scroll').scrollTo({ top, behavior: smooth ? 'smooth' : 'auto' });
  setTimeout(() => { state.programmaticScroll = false; }, 160);
  updateJumpButton();
}

function centerCurrent(force = false) {
  if (!state.currentId) return;
  if (!force && state.userScrolled) return;
  scrollToTrack(state.currentId);
}

function updateJumpButton() {
  const button = $('jump-current');
  if (!state.currentId) {
    button.hidden = true;
    return;
  }
  const index = state.sorted.findIndex((track) => track.id === state.currentId);
  if (index < 0) {
    button.hidden = true;
    return;
  }
  const height = rowHeight();
  const top = index * height;
  const bottom = top + height;
  const scrollTop = $('list-scroll').scrollTop;
  const viewport = $('list-scroll').clientHeight || 0;
  button.hidden = top >= scrollTop && bottom <= scrollTop + viewport;
}

/* ----------------------------- player UI ----------------------------- */

function clearCurrent() {
  state.currentId = null;
  if (player) {
    player.pause();
    player.track = null;
    player.audio.removeAttribute('src');
    player.audio.load();
    player.setQueue(state.sorted);
  }
  updatePlayerTrack(null);
  renderVisible();
  updateUrl();
}

function updatePlayerTrack(track) {
  const cover = $('player-cover');
  if (!track) {
    cover.replaceChildren();
    cover.innerHTML = icon('note');
    $('player-title').textContent = 'Выберите трек';
    $('player-artist').textContent = '';
    $('player-cache').hidden = true;
    $('play-btn').disabled = true;
    $('prev-btn').disabled = true;
    $('next-btn').disabled = true;
    $('seek-cached').style.width = '0%';
    updateSeek(0, 0);
    return;
  }
  $('play-btn').disabled = false;
  $('prev-btn').disabled = false;
  $('next-btn').disabled = false;
  setCover(cover, track);
  $('player-title').textContent = track.title || 'Без названия';
  $('player-artist').textContent = track.artist || 'Неизвестный исполнитель';
  $('player-cache').hidden = false;
  updatePlayerCache(track);
  updateSeek(player.currentTime, player.duration);
}

function updatePlayerCache(track) {
  const button = $('player-cache');
  if (!track) {
    button.hidden = true;
    return;
  }
  button.hidden = false;
  updateCacheIndicator(button, track);
  const status = cacheManager ? cacheManager.state(track) : { status: 'none', progress: 0 };
  $('seek-cached').style.width = status.status === 'caching' ? `${(status.progress || 0) * 100}%` : '0%';
}

function updateSeek(current, duration) {
  const progress = duration ? Math.min(1, current / duration) : 0;
  const percent = `${progress * 100}%`;
  $('seek-played').style.width = percent;
  $('seek-thumb').style.left = percent;
  $('time-current').textContent = formatTime(current);
  $('time-total').textContent = formatTime(duration);
  const seek = $('seek');
  seek.setAttribute('aria-valuemax', String(Math.round(duration || 0)));
  seek.setAttribute('aria-valuenow', String(Math.round(current || 0)));
  seek.setAttribute('aria-valuetext', `${formatTime(current)} из ${formatTime(duration)}`);
}

function updateCacheProgress(track, progress) {
  if (!track || track.id !== state.currentId) return;
  $('seek-cached').style.width = `${Math.max(0, Math.min(1, progress)) * 100}%`;
  const row = document.querySelector(`.track-row[data-id="${track.id}"] .cache-indicator`);
  if (row) updateCacheIndicator(row, track);
}

function updateRepeatButton() {
  const button = $('repeat-btn');
  button.dataset.repeat = player.repeat;
  button.title = player.repeat === 'one' ? 'Повтор трека' : player.repeat === 'all' ? 'Повтор плейлиста' : 'Повтор выключен';
  button.setAttribute('aria-label', button.title);
}

function updateVolume() {
  const value = Math.round(player.volume * 100);
  $('volume-fill').style.width = `${value}%`;
  $('volume').setAttribute('aria-valuenow', String(value));
  $('player-extra').classList.toggle('is-muted', player.muted);
}

function onTrackChange(track) {
  state.currentId = track ? track.id : null;
  if (track && state.playlistId) {
    localStorage.setItem(`soundsave:current:${state.playlistId}`, String(track.id));
  }
  updatePlayerTrack(track);
  renderVisible();
  centerCurrent();
  updateUrl();
  updateJumpButton();
}

function onTime({ current, duration }) {
  updateSeek(current, duration);
  if (!state.draggingSeek) {
    $('seek-played').style.width = `${duration ? Math.min(1, current / duration) * 100 : 0}%`;
    $('seek-thumb').style.left = `${duration ? Math.min(1, current / duration) * 100 : 0}%`;
  }
  if (state.playlistId && state.currentId && Date.now() - state.lastPositionSave > 2000) {
    state.lastPositionSave = Date.now();
    localStorage.setItem(`soundsave:position:${state.playlistId}`, String(current));
  }
}

function onPlayerState({ playing, repeat, volume, muted }) {
  if (typeof playing === 'boolean') {
    $('play-btn').classList.toggle('is-playing', playing);
    $('play-btn').title = playing ? 'Пауза' : 'Воспроизвести';
    $('play-btn').setAttribute('aria-label', playing ? 'Пауза' : 'Воспроизвести');
    if ('mediaSession' in navigator) navigator.mediaSession.playbackState = playing ? 'playing' : 'paused';
    if (playing && cacheManager && cacheManager.autoCache && state.currentId) {
      const track = currentTrack();
      if (track) autoCache(track);
    }
  }
  if (repeat) updateRepeatButton();
  if (typeof volume === 'number' || typeof muted === 'boolean') updateVolume();
}

function currentTrack() {
  return state.sorted.find((track) => track.id === state.currentId) || state.tracks.find((track) => track.id === state.currentId) || null;
}

function playbackSource(track) {
  if (!track || !track.audio_url) return '';
  if (cacheManager && cacheManager.isCached(track)) return track.audio_url;
  // Do not route the live stream through the service worker: Android can
  // freeze the worker with the page, and the audio then dies when its buffer
  // runs out. Only cached files are served by the worker.
  const separator = track.audio_url.includes('?') ? '&' : '?';
  return `${track.audio_url}${separator}stream=1`;
}

function autoCache(track) {
  if (!cacheManager || !cacheManager.available) return;
  if (document.visibilityState !== 'visible' || navigator.onLine === false) return;
  const status = cacheManager.state(track).status;
  if (status === 'cached' || status === 'caching') return;
  cacheManager.download(track).catch((error) => {
    if (!document.hidden) toast(error.message, { error: true });
  });
}

function downloadTrack(track) {
  if (!cacheManager || !cacheManager.available) {
    toast('Браузер не поддерживает локальный кэш', { error: true });
    return;
  }
  if (cacheManager.isCached(track)) {
    toast('Трек уже сохранён локально');
    return;
  }
  if (cacheManager.state(track).status === 'caching') return;
  cacheManager.download(track).then(() => toast('Трек сохранён локально')).catch((error) => toast(error.message, { error: true }));
}

/* ----------------------------- cache UI ----------------------------- */

function onCacheChange() {
  if (!cacheManager) return;
  const track = currentTrack();
  if (track) updatePlayerCache(track);
  renderVisible();
  updateCacheUsage();
}

function updateCacheUsage() {
  if (!cacheManager) return;
  cacheManager.usage().then((bytes) => {
    $('cache-usage').textContent = formatBytes(bytes);
  }).catch(() => {});
}

/* ----------------------------- sync ----------------------------- */

function showSyncBanner(text, progress = 0) {
  $('sync-banner').hidden = false;
  state.syncHidden = false;
  $('sync-text').textContent = text;
  $('sync-progress-fill').style.width = `${Math.max(0, Math.min(1, progress)) * 100}%`;
}

function hideSyncBanner() {
  $('sync-banner').hidden = true;
}

async function startSync() {
  const playlist = currentPlaylist();
  if (!playlist || !playlist.syncable) return;
  try {
    const job = await api(`/api/playlists/${playlist.id}/sync`, { method: 'POST' });
    state.syncHidden = false;
    state.syncTimer = -1;
    showSyncBanner('Синхронизация…', 0);
    updateSyncButton();
    pollSync(job);
  } catch (error) {
    toast(error.message, { error: true });
  }
}

async function pollSync() {
  const playlist = currentPlaylist();
  if (!playlist) return;
  clearTimeout(state.syncTimer);
  state.syncTimer = setTimeout(async () => {
    try {
      const job = await api(`/api/playlists/${playlist.id}/sync`);
      const total = job.total || 0;
      const completed = job.completed || 0;
      const progress = total ? completed / total : 0;
      const current = [job.current_artist, job.current_title].filter(Boolean).join(' — ');
      if (job.state === 'running') {
        if (!state.syncHidden) {
          showSyncBanner(current ? `${completed} из ${total} · ${current}` : 'Синхронизация…', progress);
        }
        state.syncTimer = null;
        pollSync();
        return;
      }
      state.syncTimer = null;
      updateSyncButton();
      if (job.state === 'error') {
        showSyncBanner(job.error || 'Ошибка синхронизации', progress);
        toast(job.error || 'Ошибка синхронизации', { error: true, timeout: 8000 });
        return;
      }
      if (job.state === 'done') {
        showSyncBanner(job.limit_reached ? `Скачано ${job.added}; достигнут MAX_DOWNLOADS` : `Готово: добавлено ${job.added}`, 1);
        if (job.added) toast(`Добавлено треков: ${job.added}`);
        if (job.limit_reached) toast('Достигнут MAX_DOWNLOADS — нажмите «Синхронизировать» ещё раз', { timeout: 7000 });
        await loadPlaylists({ preserveSelection: true });
        setTimeout(() => hideSyncBanner(), 5000);
      }
    } catch (error) {
      state.syncTimer = null;
      updateSyncButton();
      toast(error.message, { error: true });
    }
  }, 900);
}

/* ----------------------------- events ----------------------------- */

function setupEvents() {
  $('playlist-select').addEventListener('change', (event) => {
    selectPlaylist(Number(event.target.value)).catch((error) => toast(error.message, { error: true }));
  });
  $('sort-control').addEventListener('click', (event) => {
    const button = event.target.closest('[data-sort]');
    if (!button) return;
    const sort = button.dataset.sort;
    applySort(sort, sort === state.sort && state.sortDir === 'asc' ? 'desc' : 'asc');
  });
  $('sort-dir').addEventListener('click', (event) => {
    event.stopPropagation();
    applySort(state.sort, state.sortDir === 'asc' ? 'desc' : 'asc');
  });
  $('theme-btn').addEventListener('click', cycleTheme);
  $('settings-btn').addEventListener('click', openSettings);
  $('refresh-btn').addEventListener('click', refreshLibrary);
  $('sync-btn').addEventListener('click', startSync);
  $('sync-hide').addEventListener('click', () => {
    state.syncHidden = true;
    hideSyncBanner();
  });
  $('play-btn').addEventListener('click', () => player.toggle());
  $('prev-btn').addEventListener('click', () => player.previous());
  $('next-btn').addEventListener('click', () => player.next());
  $('repeat-btn').addEventListener('click', () => {
    player.cycleRepeat();
    updateRepeatButton();
  });
  $('mute-btn').addEventListener('click', () => player.toggleMute());
  $('player-cache').addEventListener('click', () => {
    const track = currentTrack();
    if (track) downloadTrack(track);
  });
  $('jump-current').addEventListener('click', () => {
    state.userScrolled = false;
    centerCurrent(true);
    $('jump-current').hidden = true;
  });

  $('list-scroll').addEventListener('scroll', () => {
    if (!state.programmaticScroll) state.userScrolled = true;
    renderVisible();
    updateJumpButton();
  }, { passive: true });

  const resizeObserver = new ResizeObserver(() => {
    renderVisible();
    if (!state.userScrolled) centerCurrent(true);
  });
  resizeObserver.observe($('list-scroll'));
  MOBILE_QUERY.addEventListener('change', () => {
    renderList();
    centerCurrent(true);
  });

  setupSeek();
  setupVolume();
  setupSettings();
  setupKeyboard();
  setupInstallPrompt();
  window.addEventListener('online', () => { toast('Соединение восстановлено'); updateSyncButton(); });
  window.addEventListener('offline', () => toast('Нет сети — доступны только скачанные треки', { error: true }));
  window.addEventListener('pagehide', savePosition);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      state.wasPlayingBeforeHidden = Boolean(player && player.playing);
      savePosition();
      if (cacheManager) cacheManager.cancelActive();
      return;
    }
    if (state.wasPlayingBeforeHidden && player && player.audio.paused) {
      player.play();
    }
    state.wasPlayingBeforeHidden = false;
  });
}

function savePosition() {
  if (!state.playlistId || !state.currentId || !player) return;
  localStorage.setItem(`soundsave:position:${state.playlistId}`, String(player.currentTime));
}

function setupSeek() {
  const seek = $('seek');
  const ratio = (event) => {
    const rect = seek.getBoundingClientRect();
    return Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  };
  seek.addEventListener('pointerdown', (event) => {
    if (!player.duration) return;
    state.draggingSeek = true;
    seek.setPointerCapture(event.pointerId);
    player.seek(ratio(event) * player.duration);
  });
  seek.addEventListener('pointermove', (event) => {
    if (!state.draggingSeek) return;
    player.seek(ratio(event) * player.duration);
  });
  const finish = () => { state.draggingSeek = false; };
  seek.addEventListener('pointerup', finish);
  seek.addEventListener('pointercancel', finish);
  seek.addEventListener('keydown', (event) => {
    if (!player.duration) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      player.seek(player.currentTime + (event.key === 'ArrowLeft' ? -5 : 5));
    } else if (event.key === 'Home') {
      event.preventDefault();
      player.seek(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      player.seek(player.duration);
    }
  });
}

function setupVolume() {
  const volume = $('volume');
  const set = (event) => {
    const rect = volume.getBoundingClientRect();
    player.setVolume((event.clientX - rect.left) / rect.width);
    updateVolume();
  };
  volume.addEventListener('pointerdown', (event) => {
    state.draggingVolume = true;
    volume.setPointerCapture(event.pointerId);
    set(event);
  });
  volume.addEventListener('pointermove', (event) => { if (state.draggingVolume) set(event); });
  volume.addEventListener('pointerup', () => { state.draggingVolume = false; });
  volume.addEventListener('pointercancel', () => { state.draggingVolume = false; });
  volume.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      player.setVolume(player.volume + (event.key === 'ArrowLeft' ? -0.05 : 0.05));
      updateVolume();
    }
  });
}

function setupSettings() {
  const dialog = $('settings-dialog');
  $('theme-select').addEventListener('change', (event) => setTheme(event.target.value));
  $('auto-cache').addEventListener('change', (event) => cacheManager.setAuto(event.target.checked));
  $('cache-limit').addEventListener('change', (event) => {
    cacheManager.setLimitMb(Number(event.target.value));
    updateCacheUsage();
  });
  $('clear-cache').addEventListener('click', async () => {
    if (!confirm('Очистить локальный кэш аудио?')) return;
    await cacheManager.clear();
    updateCacheUsage();
    toast('Кэш очищен');
  });
  $('install-btn').addEventListener('click', async () => {
    if (!state.installPrompt) return;
    state.installPrompt.prompt();
    await state.installPrompt.userChoice;
    state.installPrompt = null;
    $('install-btn').hidden = true;
  });
  dialog.addEventListener('close', () => {});
}

function openSettings() {
  const dialog = $('settings-dialog');
  $('theme-select').value = themePreference();
  $('auto-cache').checked = Boolean(cacheManager && cacheManager.autoCache);
  $('cache-limit').value = localStorage.getItem('soundsave:cacheLimitMb') || '2048';
  updateCacheUsage();
  if (typeof dialog.showModal === 'function') dialog.showModal();
  else dialog.setAttribute('open', '');
}

function setupKeyboard() {
  document.addEventListener('keydown', (event) => {
    const tag = event.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || tag === 'BUTTON') return;
    if (document.querySelector('dialog[open]')) return;
    if (event.code === 'Space') {
      event.preventDefault();
      player.toggle();
    } else if (event.key === 'ArrowRight' && event.altKey) {
      event.preventDefault();
      player.next();
    } else if (event.key === 'ArrowLeft' && event.altKey) {
      event.preventDefault();
      player.previous();
    }
  });
}

async function refreshLibrary() {
  const button = $('refresh-btn');
  button.disabled = true;
  toast('Обновление библиотеки…');
  try {
    await api('/api/refresh', { method: 'POST' });
    await loadPlaylists({ preserveSelection: true });
    toast('Библиотека обновлена');
  } catch (error) {
    toast(error.message, { error: true });
  } finally {
    button.disabled = false;
  }
}

function setupInstallPrompt() {
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    state.installPrompt = event;
    $('install-btn').hidden = false;
  });
  window.addEventListener('appinstalled', () => {
    state.installPrompt = null;
    $('install-btn').hidden = true;
    toast('Приложение установлено');
  });
  if (window.matchMedia('(display-mode: standalone)').matches) $('install-btn').hidden = true;
}

async function loadVersion() {
  try {
    const data = await api('/api/version');
    $('version').textContent = data.version || '';
  } catch (error) { /* version is optional */ }
}

function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('/sw.js').then((registration) => {
    registration.addEventListener('updatefound', () => {
      const worker = registration.installing;
      if (!worker) return;
      worker.addEventListener('statechange', () => {
        if (worker.state === 'installed' && navigator.serviceWorker.controller) {
          toast('Доступно обновление приложения — перезагрузите страницу', { timeout: 8000 });
        }
      });
    });
  }).catch((error) => console.warn('Service worker registration failed', error));
}

async function init() {
  applyTheme(themePreference());
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  systemTheme.addEventListener('change', () => {
    if (themePreference() === 'system') applyTheme('system');
  });
  player = new Player({
    onTrackChange,
    onTime,
    onState: onPlayerState,
    onError: (message) => toast(message, { error: true }),
    resolveSource: playbackSource,
  });
  cacheManager = new CacheManager({
    onChange: onCacheChange,
    onProgress: updateCacheProgress,
  });
  await cacheManager.init();
  cacheManager.requestPersist();
  $('auto-cache').checked = cacheManager.autoCache;
  $('cache-limit').value = localStorage.getItem('soundsave:cacheLimitMb') || '2048';
  const params = new URLSearchParams(location.search);
  const sort = params.get('sort');
  if (sort === 'title' || sort === 'artist' || sort === 'number') state.sort = sort;
  const dir = params.get('dir');
  if (dir === 'asc' || dir === 'desc') state.sortDir = dir;
  applySort(state.sort, state.sortDir);
  setupEvents();
  updateRepeatButton();
  updateVolume();
  updatePlayerTrack(null);
  await loadVersion();
  await loadPlaylists();
  registerServiceWorker();
}

init().catch((error) => {
  console.error(error);
  toast(`Не удалось запустить приложение: ${error.message}`, { error: true, timeout: 10000 });
});
