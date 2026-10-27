/* Audio playback, repeat modes and Media Session integration. */

export class Player {
  constructor({ onTrackChange, onTime, onState, onError, resolveSource } = {}) {
    this.audio = new Audio();
    this.audio.preload = 'auto';
    this.audio.setAttribute('playsinline', '');
    this.audio.setAttribute('aria-hidden', 'true');
    this.audio.style.display = 'none';
    // Keep the media element in the document: some Android Chromium builds
    // handle background playback more reliably when it is not detached.
    document.body.appendChild(this.audio);
    this.resolveSource = resolveSource || ((track) => track.audio_url);
    this.queue = [];
    this.track = null;
    this.repeat = localStorage.getItem('soundsave:repeat') || 'off';
    if (!['off', 'all', 'one'].includes(this.repeat)) this.repeat = 'off';
    this.volume = Number(localStorage.getItem('soundsave:volume') ?? '1');
    if (!Number.isFinite(this.volume)) this.volume = 1;
    this.muted = false;
    this.startAt = 0;
    this.onTrackChange = onTrackChange || (() => {});
    this.onTime = onTime || (() => {});
    this.onState = onState || (() => {});
    this.onError = onError || (() => {});
    this.audio.volume = this.volume;
    this._wireAudio();
    this._wireMediaSession();
  }

  _wireAudio() {
    this.audio.addEventListener('timeupdate', () => this._emitTime());
    this.audio.addEventListener('durationchange', () => this._emitTime());
    this.audio.addEventListener('progress', () => this._emitTime());
    this.audio.addEventListener('play', () => this.onState({ playing: true, loading: false }));
    this.audio.addEventListener('pause', () => this.onState({ playing: false, loading: false }));
    this.audio.addEventListener('waiting', () => this.onState({ playing: !this.audio.paused, loading: true }));
    this.audio.addEventListener('canplay', () => this.onState({ playing: !this.audio.paused, loading: false }));
    this.audio.addEventListener('loadedmetadata', () => {
      if (this.startAt > 0 && Number.isFinite(this.audio.duration)) {
        this.audio.currentTime = Math.min(this.startAt, Math.max(0, this.audio.duration - 0.5));
        this.startAt = 0;
      }
      this._emitTime();
      this._updatePositionState();
    });
    this.audio.addEventListener('ended', () => this._handleEnded());
    this.audio.addEventListener('error', () => {
      const code = this.audio.error ? this.audio.error.code : 0;
      const message = code === 4
        ? 'Формат не поддерживается браузером'
        : code === 2
          ? 'Ошибка сети при загрузке трека'
          : 'Не удалось воспроизвести трек';
      this.onError(message);
      this.onState({ playing: false, loading: false });
    });
  }

  _wireMediaSession() {
    if (!('mediaSession' in navigator)) return;
    const actions = {
      play: () => this.play(),
      pause: () => this.pause(),
      previoustrack: () => this.previous(),
      nexttrack: () => this.next(),
      seekbackward: (details) => this.seek(this.audio.currentTime - (details.seekOffset || 10)),
      seekforward: (details) => this.seek(this.audio.currentTime + (details.seekOffset || 10)),
      seekto: (details) => {
        if (typeof details.seekTime === 'number') this.seek(details.seekTime);
      },
    };
    for (const [action, handler] of Object.entries(actions)) {
      try { navigator.mediaSession.setActionHandler(action, handler); } catch (error) { /* unsupported */ }
    }
  }

  setQueue(tracks) {
    this.queue = Array.isArray(tracks) ? tracks : [];
  }

  find(trackId) {
    return this.queue.find((track) => track.id === Number(trackId)) || null;
  }

  setTrack(track, { autoplay = false, startAt = 0 } = {}) {
    if (!track) return;
    const same = this.track && this.track.id === track.id;
    this.track = track;
    this.startAt = Number(startAt) || 0;
    if (!same) {
      this.audio.pause();
      this.audio.src = this.resolveSource(track);
      this.audio.load();
    }
    this.onTrackChange(track);
    this._updateMetadata();
    if (autoplay) this.play();
    else this.onState({ playing: false, loading: false });
  }

  playTrack(trackId, { startAt = 0 } = {}) {
    const track = this.find(trackId);
    if (!track) return;
    if (this.track && this.track.id === track.id) {
      if (this.audio.paused) this.play();
      else this.pause();
      return;
    }
    this.setTrack(track, { autoplay: true, startAt });
  }

  play() {
    if (!this.track) {
      if (this.queue.length) this.setTrack(this.queue[0], { autoplay: true });
      return;
    }
    const result = this.audio.play();
    if (result && typeof result.catch === 'function') {
      result.catch(() => this.onState({ playing: false, loading: false }));
    }
  }

  pause() {
    this.audio.pause();
  }

  toggle() {
    if (this.audio.paused) this.play();
    else this.pause();
  }

  next({ manual = true } = {}) {
    if (!this.track || !this.queue.length) return;
    const index = this.queue.findIndex((track) => track.id === this.track.id);
    let nextIndex = index + 1;
    if (nextIndex >= this.queue.length) {
      if (this.repeat === 'all') nextIndex = 0;
      else {
        this.audio.pause();
        if (!manual) this.audio.currentTime = 0;
        return;
      }
    }
    this.setTrack(this.queue[nextIndex], { autoplay: true });
  }

  previous() {
    if (!this.track || !this.queue.length) return;
    if (this.audio.currentTime > 3) {
      this.seek(0);
      return;
    }
    const index = this.queue.findIndex((track) => track.id === this.track.id);
    let previousIndex = index - 1;
    if (previousIndex < 0) {
      if (this.repeat === 'all') previousIndex = this.queue.length - 1;
      else {
        this.seek(0);
        return;
      }
    }
    this.setTrack(this.queue[previousIndex], { autoplay: true });
  }

  _handleEnded() {
    if (!this.track) return;
    if (this.repeat === 'one') {
      this.audio.currentTime = 0;
      this.play();
      return;
    }
    const index = this.queue.findIndex((track) => track.id === this.track.id);
    if (index + 1 < this.queue.length || this.repeat === 'all') {
      this.next({ manual: false });
    } else {
      this.onState({ playing: false, loading: false });
    }
  }

  seek(seconds) {
    if (!Number.isFinite(this.audio.duration)) return;
    const value = Math.max(0, Math.min(this.audio.duration, Number(seconds) || 0));
    this.audio.currentTime = value;
    this._emitTime();
    this._updatePositionState();
  }

  setRepeat(value) {
    this.repeat = ['off', 'all', 'one'].includes(value) ? value : 'off';
    localStorage.setItem('soundsave:repeat', this.repeat);
    this.onState({ repeat: this.repeat });
  }

  cycleRepeat() {
    const order = ['off', 'all', 'one'];
    this.setRepeat(order[(order.indexOf(this.repeat) + 1) % order.length]);
  }

  setVolume(value) {
    this.volume = Math.max(0, Math.min(1, Number(value)));
    this.audio.volume = this.volume;
    this.muted = false;
    localStorage.setItem('soundsave:volume', String(this.volume));
    this.onState({ volume: this.volume, muted: false });
  }

  toggleMute() {
    this.muted = !this.muted;
    this.audio.muted = this.muted;
    this.onState({ volume: this.volume, muted: this.muted });
  }

  get currentTime() { return this.audio.currentTime || 0; }
  get duration() { return Number.isFinite(this.audio.duration) ? this.audio.duration : 0; }
  get playing() { return !this.audio.paused; }

  _emitTime() {
    const duration = this.duration;
    const current = Math.min(this.currentTime, duration || this.currentTime);
    this.onTime({
      current,
      duration,
      progress: duration ? current / duration : 0,
    });
  }

  _updateMetadata() {
    if (!('mediaSession' in navigator) || !this.track) return;
    const artwork = this.track.cover_url
      ? [{ src: this.track.cover_url, sizes: '256x256', type: 'image/webp' }]
      : [];
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: this.track.title || '',
        artist: this.track.artist || '',
        album: this.track.album || '',
        artwork,
      });
    } catch (error) {
      /* MediaMetadata is not available in every browser */
    }
  }

  _updatePositionState() {
    if (!('mediaSession' in navigator) || !navigator.mediaSession.setPositionState) return;
    const duration = this.duration;
    if (!duration) return;
    try {
      navigator.mediaSession.setPositionState({
        duration,
        playbackRate: this.audio.playbackRate || 1,
        position: Math.min(this.currentTime, duration),
      });
    } catch (error) {
      /* ignore invalid position states */
    }
  }
}
