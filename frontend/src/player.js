/* Плеер: singleton audio + очередь + подписка на состояние.
   Слой D (playback engine): единый Audio на всё приложение; источник —
   локальный файл (если трек сохранён офлайн, §4/§7), иначе серверный
   стрим. Здесь же MediaSession (§9), восстановление сессии (§10),
   офлайн-события истории (§17). Любой сбой локального слоя не должен
   ломать онлайн-плеер: всё в try/catch с фолбэком на стрим. */
import { useSyncExternalStore } from 'react';
import { API_BASE, api, playerSrc, coverUrl } from './api.js';
import { localSrc, addHistory, recordEvent, syncEvents, cacheTracks } from './local/repo.js';
import { saveSession, saveSessionSoon, getSession } from './local/recover.js';
import * as ms from './local/mediasession.js';
import { bootLockAnime, lockAnimeState, onLockFrame, setLockPro } from './local/lockanime.js';
import { boot as bootDownloads } from './local/downloads.js';

/* iOS/WebKit: mediaSession.metadata.artwork игнорируется — обложка Now
   Playing (экран блокировки) берётся из POSTER видео-элемента. Поэтому
   «аудио» — это <video> (поток обычного аудио играет без картинки),
   спрятанный за экраном: display:none iOS может считать неиграбельным.
   Постер тянки подставляет lockanime (onLockFrame ниже). */
const audio = document.createElement('video');
audio.preload = 'metadata';
audio.playsInline = true;
audio.setAttribute('playsinline', '');
try { audio.webkitWirelessVideoPlaybackDisabled = true; } catch { /* не WebKit */ }
try { audio.disableRemotePlayback = true; } catch { /* не поддерживается */ }
try { audio.setAttribute('poster', ''); } catch { /* ок */ }
try {
  const el = document.body || document.documentElement;
  el.appendChild(audio);
  audio.style.cssText = 'position:fixed;left:-9999px;top:0;width:2px;height:2px;opacity:0;pointer-events:none;';
} catch { /* элемент вне DOM — играем и так */ }

const savedVol = parseFloat(localStorage.getItem('vol'));
const initVolume = isNaN(savedVol) ? 1 : Math.min(1, Math.max(0, savedVol));
/* Последняя ненулевая громкость — отдельный ключ: если приложение
   закрыли в mute, размьют после перезагрузки вернёт прежний уровень,
   а не 100%. */
const savedLastVol = parseFloat(localStorage.getItem('vol_last'));
const initLastVolume = !isNaN(savedLastVol) && savedLastVol > 0
  ? Math.min(1, savedLastVol)
  : (initVolume > 0 ? initVolume : 1);
try { audio.volume = initVolume; } catch { /* webview strictness */ }

/* живая обложка: каждый кадр тянки → poster видео-элемента (iOS локскрин) */
onLockFrame(url => { if (url) { try { audio.poster = url; } catch { /* ок */ } } });

let state = {
  queue: [],
  index: -1,
  playing: false,
  loading: false,
  error: null,
  attempts: 0,
  currentTime: 0,
  duration: 0,
  shuffle: false,
  loop: false,
  volume: initVolume,
  local: false,          // играет локальный файл (офлайн)
  pendingSeek: null,     // позиция для восстановления сессии (§10)
};

const listeners = new Set();

function emit() {
  state.playing = !audio.paused && !audio.ended;
  state.currentTime = audio.currentTime || 0;
  state.duration = audio.duration || 0;
  state = { ...state };            // новый снапшот: useSyncExternalStore видит изменение
  for (const fn of listeners) fn();
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/* Хук для подписки React-компонентов на состояние плеера. */
export function usePlayerSnap() {
  return useSyncExternalStore(subscribe, getState, getState);
}

/* Облегчённая подписка для длинных списков (TrackRow × 500+): полный
   снапшот меняется на каждом timeupdate (~4 Гц) и заставлял перерисовываться
   весь трек-лист во время воспроизведения — главный источник дёрганий.
   Здесь снапшот — примитив «id:playing»: React сравнивает по значению,
   поэтому строка перерисовывается только при смене трека или play/pause. */
export function useCurKey() {
  return useSyncExternalStore(
    subscribe,
    () => `${state.queue[state.index]?.id ?? ''}:${state.playing ? 1 : 0}`,
    () => '-:-',
  );
}

export function getState() { return state; }

export function current() {
  return state.queue[state.index] || null;
}

export async function play(list, index, resumeSec = null, keepWave = false) {
  const track = list[index];
  if (!track) return;
  if (!keepWave) setWaveSource(null);
  state.queue = [...list];
  state.index = index;
  state.pendingSeek = Number.isFinite(resumeSec) ? Math.max(0, resumeSec) : null;
  await loadAndPlay();
  try { await cacheTracks(list); } catch { /* кеш метаданных — best effort */ }
}

async function loadAndPlay() {
  const track = current();
  if (!track) return;
  state.loading = true;
  state.error = null;
  state.attempts = 0;
  beaconUid = null;               // новый трек → свежий старт-беакон
  emit();
  /* MediaSession §9: обложка/название/исполнитель для системного плеера */
  ms.setMetadata({
    title: track.title, artist: track.artist, album: track.album,
    artwork: coverUrl(track),
  });
  await startUrl(track.id);
  saveSessionSoon(sessData());
}

async function startUrl(trackId) {
  /* трек берём из очереди: у главы книги это объект с audio_url/skipLocalLookup */
  const track = current()
    ?? state.queue.find(t => String(t.id) === String(trackId));
  if (!track) return;
  state.loading = true;
  emit();
  try {
    let src = null, kind = null, isLocal = false;
    if (!track.skipLocalLookup) {
      try {
        src = await localSrc(trackId);         // OPFS/IDB → blob URL
        isLocal = !!src;
      } catch { src = null; }
    }
    if (!src && track.audio_url) {
      src = /^https?:/.test(track.audio_url) ? track.audio_url : API_BASE + track.audio_url;
      kind = 'podcast';
    } else if (!src) {
      const r = await playerSrc(trackId);      // { url, kind? }
      src = r.url || r;
      kind = r.kind || null;
    } else {
      kind = 'local';
    }
    if (lastUrl && lastLocal) { try { URL.revokeObjectURL(lastUrl); } catch { /* ок */ } }
    lastUrl = isLocal ? src : null;
    lastLocal = isLocal;
    state.kind = kind;
    state.local = isLocal;
    audio.src = src;
    await audio.play();
    ms.bindActions(MS_ACTIONS);      // iOS: кнопки локскрина после старта
  } catch (e) {
    if (e && e.name === 'AbortError') { state.loading = false; emit(); return; }
    state.error = (e && e.message) || 'не удалось открыть трек';
  }
  state.loading = false;
  emit();
}

let lastUrl = null, lastLocal = false;

const sleep = ms => new Promise(r => setTimeout(r, ms));

/* Волна: режим бесконечного потока. Wave.jsx задаёт источник:
   { sessionId, fetchMore(last) -> tracks[], onEvent(kind, track, playedSec) }
   kind: started | skip | finished | played_10s | played_30s | played_50p |
         played_90p | seeked | replayed. Внутри партии игрок движется локально,
   фидбек уходит через /feedback; на исчерпании партии — /next за новой.
   Гранулярные метки (§7.1) эмитятся однократно на трек — телеметрия для
   метрик и адаптации волны. */
let wave = null;

export function setWaveSource(src) { wave = src || null; }
export function inWave() { return !!wave; }

/* Прогресс текущего трека в волне: однократные метки + максимум дослушивания
   (нужен, чтобы отличить replay от повторного старта после паузы). */
let wflags = null;

function waveMark() {
  const t = current();
  if (!t) return;
  if (!wflags || wflags.id !== t.id) {
    wflags = { id: t.id, m10: false, m30: false, m50: false, m90: false,
               maxSec: 0, firedReplay: false };
  }
  const cur = audio.currentTime || 0;
  if (cur > wflags.maxSec) wflags.maxSec = cur;
  const dur = audio.duration || t.duration || 0;
  const fire = (kind, at) => {
    if (wflags[kind]) return;
    wflags[kind] = true;
    wave.onEvent?.(kind, t, at);
  };
  if (dur > 0) {
    if (cur >= dur * 0.9) fire('played_90p', cur);
    if (cur >= dur * 0.5) fire('played_50p', cur);
  }
  if (cur >= 30) fire('played_30s', cur);
  if (cur >= 10) fire('played_10s', cur);
}

export function toggle() {
  if (!current()) return;
  if (audio.paused) audio.play().catch(() => {});
  else audio.pause();
}

export async function next(manual = true) {
  if (wave && current()) return waveNext(manual);
  if (!state.queue.length) return;
  if (manual) {
    /* ручной скип: локальное событие → идемпотентная синхронизация (§17) */
    const cur = current();
    const played = audio.currentTime || 0;
    if (cur && played > 5 && !cur.podcast && !cur.audio_url) {
      try { addHistory(cur.id, { skipped: true, played, finished: false }); } catch { /* ок */ }
      try { recordEvent({ kind: 'skipped', track_id: cur.id,
                          played_seconds: played, skipped: true }).catch(() => {}); }
      catch { /* ок */ }
    }
  }
  if (state.loop && !manual) { await loadAndPlay(); return; }
  if (state.shuffle && state.queue.length > 1) {
    let r;
    do { r = Math.floor(Math.random() * state.queue.length); } while (r === state.index);
    state.index = r;
  } else if (state.index < state.queue.length - 1) {
    state.index += 1;
  } else if (!manual) {
    state.index = 0; // по кругу для фонового проигрывания
  } else {
    emit();
    return;
  }
  await loadAndPlay();
}

/* Продолжение волны: внутри партии — локальное продвижение + ленивый фидбек;
   партия исчерпана — /next (фидбек уходит в last), дозарядка новых треков. */
async function waveNext(manual) {
  const cur = current();
  const played = audio.currentTime || 0;
  const ev = manual ? 'skip' : 'finished';

  if (state.index < state.queue.length - 1) {
    if (cur) wave.onEvent?.(ev, cur, played);
    state.index += 1;
    await loadAndPlay();
    return;
  }

  let more = null;
  state.loading = true;
  emit();
  try {
    more = await wave.fetchMore({ track_id: cur?.id, played_seconds: played, event: ev });
  } catch (e) {
    state.loading = false;
    state.playing = false;
    state.error = (e && e.message) || 'волна прервалась';
    emit();
    return;
  }
  if (!more || !more.length) {
    state.loading = false;
    state.playing = false;
    state.error = 'волна стихла — запусти заново';
    emit();
    return;
  }
  state.queue = [...state.queue, ...more.filter(t => !state.queue.some(x => x.id === t.id))];
  state.index += 1;
  await loadAndPlay();
}

export async function prev() {
  if (state.index > 0) {
    state.index -= 1;
    await loadAndPlay();
  } else {
    audio.currentTime = 0;
  }
}

/* Встряска волны: всё, что ещё не отыграно, заменяем новой партией
   (текущий трек продолжает играть до конца — переход бесшовный). */
export function replaceUpcoming(tracks) {
  if (!wave) return;
  const keep = state.queue.slice(0, state.index + 1);
  const ids = new Set(keep.map(t => t.id));
  const add = (tracks || []).filter(t => !ids.has(t.id));
  state.queue = [...keep, ...add];
  emit();
}

export function seek(p) {
  if (!audio.duration) return;
  const t = Math.max(0, Math.min(1, +p || 0)) * audio.duration;
  const jump = Math.abs(t - (audio.currentTime || 0));
  audio.currentTime = t;
  state.currentTime = t;           // мгновенный отклик шкалы, без ожидания timeupdate
  if (wave && current() && jump > 5) wave.onEvent?.('seeked', current(), t);
  emit();
}

export function setShuffle(v) { state.shuffle = v; emit(); }
export function setLoop(v) { state.loop = v; emit(); }

let lastVolume = initLastVolume;

export function setVolume(v) {
  state.volume = Math.min(1, Math.max(0, +v || 0));
  if (state.volume > 0) {
    lastVolume = state.volume;
    localStorage.setItem('vol_last', String(state.volume));
  }
  try { audio.volume = state.volume; } catch { /* webview */ }
  localStorage.setItem('vol', String(state.volume));
  emit();
}

/* Тап по динамику: 0 ↔ прежняя громкость (а не «сразу на максимум»). */
export function toggleMute() {
  setVolume(state.volume > 0 ? 0 : (lastVolume || 1));
}

/* ---------------- история: беакон веб-плеера (вне волны) ----------------
   Волна пишет свои события (/feedback), здесь только обычные списки:
   started — при первом воспроизведении трека, finished — по завершении. */
let beaconUid = null;

function beacon(finished, played) {
  const t = current();
  if (!t || t.podcast || t.audio_url || !Number.isSafeInteger(Number(t.id))) return;
  if (!finished) {
    if (beaconUid === t.id) return;
    beaconUid = t.id;
  }
  try {
    addHistory(t.id, { finished, played, local: state.local });   // локальная история
  } catch { /* не критично */ }
  api('/api/history', { method: 'POST', body: JSON.stringify({
    track_id: t.id, finished: !!finished,
    played_seconds: Math.max(0, Math.round(played || 0)) }) })
    .catch(() => {
      /* сеть упала — событие уйдёт позже идемпотентно (§17) */
      recordEvent({ kind: finished ? 'finished' : 'started', track_id: t.id,
                    played_seconds: played, finished }).catch(() => {});
    });
}

let startedId = null;            // последний трек, для которого ушли «started»

audio.addEventListener('loadedmetadata', () => {
  /* восстановление позиции §10: применяем один раз после готовности метаданных */
  if (state.pendingSeek != null && Number.isFinite(audio.duration)) {
    try { audio.currentTime = Math.min(state.pendingSeek, Math.max(0, audio.duration - 2)); }
    catch { /* webview strictness */ }
    state.pendingSeek = null;
  }
  ms.setPositionState({ duration: audio.duration, position: audio.currentTime, rate: audio.playbackRate });
});

audio.addEventListener('play', () => {
  state.attempts = 0;
  ms.setPlaybackState(true);
  lockAnimeState(true);            // живая обложка локскрина: тянка просыпается
});
/* iOS: handlers действуют только когда элемент уже «Now Playing» —
   ставим их и маячим старт именно на событии playing */
audio.addEventListener('playing', () => {
  ms.bindActions(MS_ACTIONS);      // iOS: ⏮/⏭ появляются именно здесь
  warmNext();                      // пока играет — заранее резолвим следующий
  if (wave && current()) {
    const cur = current();
    const dur = audio.duration || cur.duration || 0;
    const isReplay = wflags && wflags.id === cur.id && !wflags.firedReplay
      && dur > 0 && wflags.maxSec >= dur * 0.9;
    if (isReplay) {
      /* тот же трек дослушан почти до конца и запущен заново → replay */
      wflags.firedReplay = true;
      wave.onEvent?.('replayed', cur, 0);
      wave.onEvent?.('started', cur, 0);
    } else if (startedId !== cur.id) {
      /* свежий старт; resume после паузы события не порождает */
      startedId = cur.id;
      wave.onEvent?.('started', cur, 0);
    }
  } else beacon(false, 0);
  saveSession(sessData());
  emit();
});
audio.addEventListener('pause', () => {
  ms.setPlaybackState(false);
  lockAnimeState(false);           // тянка спит — видно на локскрине
  ms.setPositionState({ duration: audio.duration, position: audio.currentTime, rate: audio.playbackRate });
  saveSession(sessData());
  emit();
});
/* позиция для локскрина: не чаще раза в секунду (setPositionState дешёвый,
   но вызов на каждом timeupdate излишен) */
let lastPosMs = 0;
audio.addEventListener('timeupdate', () => {
  if (wave && current()) waveMark();
  const nowMs = Date.now();
  if (nowMs - lastPosMs >= 1000) {
    lastPosMs = nowMs;
    ms.setPositionState({ duration: audio.duration, position: audio.currentTime, rate: audio.playbackRate });
  }
  saveSessionSoon(sessData());
  emit();
});
audio.addEventListener('ended', () => {
  if (!wave && current()) beacon(true, audio.duration || audio.currentTime);
  ms.setPlaybackState(false);
  saveSession(sessData());
  next(false);
});
audio.addEventListener('error', async () => {
  // умный ретрай: гонка туннеля / устаревший токен — попробуем свежий URL
  if (state.attempts < 2 && current()) {
    state.attempts += 1;
    state.error = null;
    state.loading = true;
    emit();
    await sleep(1400);
    if (current()) await startUrl(current().id);
    return;
  }
  state.loading = false;
  state.error = state.local
    ? 'локальный файл не читается — нужен сервер'
    : 'онлайн-источник недоступен — жми «Скачать»';
  emit();
});

/* скрытие вкладки/приложения — фиксируем сессию немедленно (§10) */
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') saveSession(sessData());
  });
}

/* данные сессии для сохранения (queue id-ми — компактно) */
function sessData() {
  return {
    t: current()?.id ?? null,
    p: Math.round(audio.currentTime || 0),
    q: state.queue.map(t => t.id),
    i: state.index,
    sh: state.shuffle,
    lo: state.loop,
    v: state.volume,
  };
}

/* ---------- прогрев следующего трека (быстрый старт в очереди) ----------
   Первый старт нескачанного трека ждёт цепочку резолва на сервере
   (ЯМ → zaycev → … → yt-dlp) — десятки секунд. Пока играет текущий,
   заранее дёргаем /api/player + первые 128КБ стрима следующего: сервер
   резолвит и кеширует URL на 35 минут, и к моменту «Следующий» звук готов. */
let warmTimer = null;
function warmNext() {
  clearTimeout(warmTimer);
  if (!state.playing) return;
  const next = state.queue[state.index + 1];
  if (!next) return;
  warmTimer = setTimeout(async () => {
    try {
      if (!state.playing) return;
      const still = state.queue[state.index + 1];
      if (!still || String(still.id) !== String(next.id)) return;
      const j = await api('/api/player/' + next.id);
      if (!j?.url) return;
      const ctl = new AbortController();
      const stop = setTimeout(() => ctl.abort(), 25000);
      await fetch(API_BASE + j.url, { headers: { Range: 'bytes=0-131071' }, signal: ctl.signal });
      clearTimeout(stop);
    } catch { /* прогрев не критичен */ }
  }, 4000);
}

/* демо-очистка данных */
export function fmt(s) {
  s = Math.max(0, Math.round(s || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, '0')}`;
}

/* ---------------- Media Session: экранные кнопки ---------------- */
/* iOS: обработчики нужно переустанавливать ПОСЛЕ старта воспроизведения,
   иначе на экране блокировки могут не появиться ⏮/⏭ — поэтому байнд и
   пере-байнд после каждого удачного play(). */
const MS_ACTIONS = {
  toggle,
  next: () => { next(true).catch(() => {}); },
  prev: () => { prev().catch(() => {}); },
  seekTo: s => {
    if (!audio.duration) return;
    try { audio.currentTime = Math.max(0, Math.min(audio.duration - 1, +s || 0)); } catch { /* ок */ }
    emit();
  },
  skip: d => {
    if (!audio.duration) return;
    try {
      audio.currentTime = Math.max(0, Math.min(audio.duration - 1, audio.currentTime + (+d || 10)));
    } catch { /* ок */ }
    emit();
  },
};
ms.bindActions(MS_ACTIONS);

/* ---------------- восстановление состояния при старте (§10) ----------------
   shuffle/repeat/громкость применяем сразу; сама позиция/очередь ждут
   явного тапа «Продолжить» на Главной (autoplay-permission честно). */
(async () => {
  try {
    const s = await getSession();
    if (s) {
      if (typeof s.sh === 'boolean') state.shuffle = s.sh;
      if (typeof s.lo === 'boolean') state.loop = s.lo;
      if (typeof s.v === 'number' && s.v >= 0 && s.v <= 1) {
        try { audio.volume = s.v; state.volume = s.v; } catch { /* webview */ }
        if (s.v > 0) {
          lastVolume = s.v;
          localStorage.setItem('vol_last', String(s.v));
        }
      }
      emit();
    }
  } catch { /* состояние — best effort */ }
  try { bootDownloads(); } catch { /* загрузки не критичны */ }
  try { bootLockAnime(); } catch { /* живая обложка не критична */ }
  try {
    /* тариф обложки: Pro → эксклюзивные кадры; обновляем раз в 10 мин */
    const refreshPro = () => api('/api/billing/status')
      .then(st => setLockPro(!!(st && st.pro)))
      .catch(() => {});
    refreshPro();
    setInterval(refreshPro, 10 * 60 * 1000);
  } catch { /* ок */ }
  try { syncEvents(); } catch { /* офлайн-события уйдут позже */ }
})();
