/* Плеер: singleton audio + очередь + подписка на состояние. */
import { useSyncExternalStore } from 'react';
import { playerSrc } from './api.js';

const audio = new Audio();
audio.preload = 'metadata';

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

export function getState() { return state; }

export function current() {
  return state.queue[state.index] || null;
}

export async function play(list, index) {
  const track = list[index];
  if (!track) return;
  state.queue = [...list];
  state.index = index;
  await loadAndPlay();
}

async function loadAndPlay() {
  const track = current();
  if (!track) return;
  state.loading = true;
  state.error = null;
  state.attempts = 0;
  emit();
  await startUrl(track.id);
}

async function startUrl(trackId) {
  state.loading = true;
  emit();
  try {
    const r = await playerSrc(trackId);          // { url, kind? }
    state.kind = r.kind || null;
    audio.src = r.url || r;
    await audio.play();
  } catch (e) {
    if (e && e.name === 'AbortError') { state.loading = false; emit(); return; }
    state.error = (e && e.message) || 'не удалось открыть трек';
  }
  state.loading = false;
  emit();
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

/* Волна: режим бесконечного потока. Wave.jsx задаёт источник:
   { sessionId, fetchMore(last) -> tracks[], onEvent(kind, track, playedSec) }
   kind: started | skip | finished. Внутри партии игрок движется локально,
   фидбек уходит через /feedback; на исчерпании партии — /next за новой. */
let wave = null;

export function setWaveSource(src) { wave = src || null; }
export function inWave() { return !!wave; }

export function toggle() {
  if (!current()) return;
  if (audio.paused) audio.play().catch(() => {});
  else audio.pause();
}

export async function next(manual = true) {
  if (wave && current()) return waveNext(manual);
  if (!state.queue.length) return;
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

export function seek(p) {
  if (audio.duration) audio.currentTime = p * audio.duration;
}

export function setShuffle(v) { state.shuffle = v; emit(); }
export function setLoop(v) { state.loop = v; emit(); }

audio.addEventListener('play', () => {
  state.attempts = 0;
  if (wave && current()) wave.onEvent?.('started', current(), 0);
  emit();
});
audio.addEventListener('pause', emit);
audio.addEventListener('timeupdate', emit);
audio.addEventListener('ended', () => { next(false); });
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
  state.error = 'онлайн-источник недоступен — жми «Скачать»';
  emit();
});

/* демо-очистка данных */
export function fmt(s) {
  s = Math.max(0, Math.round(s || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, '0')}`;
}
