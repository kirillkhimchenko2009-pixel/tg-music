/* MEDIA SESSION §9 (best-effort): метаданные, состояние, экранные кнопки.
   Ничего не обещаем: API нет → тихо выходим; поведение в Telegram WebView
   проверяется на устройствах (см. план). */
export function msAvailable() {
  return typeof navigator !== 'undefined' && 'mediaSession' in navigator;
}

export function setMetadata({ title, artist, album, artwork } = {}) {
  if (!msAvailable()) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: title || '', artist: artist || '', album: album || '',
      artwork: artwork ? [{ src: artwork, sizes: '512x512', type: 'image/jpeg' }] : [],
    });
  } catch { /* не критично */ }
}

export function setPlaybackState(playing) {
  if (!msAvailable()) return;
  try { navigator.mediaSession.playbackState = playing ? 'playing' : 'paused'; }
  catch { /* ок */ }
}

/* живая обложка (локскрин-аниме): подменяет только artwork, метаданные целы */
export function setArtwork(items) {
  if (!msAvailable()) return;
  try {
    const md = navigator.mediaSession.metadata;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: md?.title || '', artist: md?.artist || '', album: md?.album || '',
      artwork: items || [],
    });
  } catch { /* ок */ }
}

/* прогресс для локскрина: полоса позиции и корректные ±10с */
export function setPositionState({ duration = 0, position = 0, rate = 1 } = {}) {
  if (!msAvailable() || typeof navigator.mediaSession.setPositionState !== 'function') return;
  try {
    if (!Number.isFinite(duration) || duration <= 0) {
      navigator.mediaSession.setPositionState();
      return;
    }
    navigator.mediaSession.setPositionState({
      duration,
      playbackRate: Number.isFinite(rate) && rate > 0 ? rate : 1,
      position: Math.min(Math.max(0, position || 0), duration),
    });
  } catch { /* ок */ }
}

/* h: { toggle, next, prev, seekTo(sec), skip(sec) } */
export function bindActions(h) {
  if (!msAvailable()) return;
  const set = (name, fn) => {
    try { navigator.mediaSession.setActionHandler(name, fn); } catch { /* нет действия */ }
  };
  set('play', () => h.toggle?.());
  set('pause', () => h.toggle?.());
  set('nexttrack', () => h.next?.());
  set('previoustrack', () => h.prev?.());
  set('seekto', d => { if (d && d.seekTime != null && Number.isFinite(d.seekTime)) h.seekTo?.(d.seekTime); });
  set('seekbackward', d => h.skip?.(-(d?.seekOffset || 10)));
  set('seekforward', d => h.skip?.(d?.seekOffset || 10));
}
