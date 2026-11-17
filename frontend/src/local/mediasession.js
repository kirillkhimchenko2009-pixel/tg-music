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
