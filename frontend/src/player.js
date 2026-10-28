/* Плеер: singleton audio + очередь + подписка на состояние. */
import { useSyncExternalStore } from 'react';
import { playerSrc } from './api.js';

const audio = new Audio();
audio.preload = 'metadata';

const state = {
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
  for (const fn of listeners) fn(state);
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/* Хук для подписки React-компонентов на состояние плеера. */
export function usePlayerSnap() {
  return useSyncExternalStore(subscribe, state, state);
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
    const url = await playerSrc(trackId);
    // преследуем мгновенный старт: src + play сразу, буферизация идёт параллельно
    audio.src = url;
    await audio.play();
  } catch (e) {
    if (e && e.name === 'AbortError') { state.loading = false; emit(); return; }
    state.error = (e && e.message) || 'не удалось открыть трек';
  }
  state.loading = false;
  emit();
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

export function toggle() {
  if (!current()) return;
  if (audio.paused) audio.play().catch(() => {});
  else audio.pause();
}

export async function next(manual = true) {
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

audio.addEventListener('play', () => { state.attempts = 0; emit(); });
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
