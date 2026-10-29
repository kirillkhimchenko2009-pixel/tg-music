/* Лайки: личный плейлист «❤ Любимое» (аналог «Мне нравится» из Яндекс.Музыки).
   Оптимистичный toggle: сердечко оживает мгновенно, при ошибке откатывается.
   Снапшоты иммутабельные — иначе useSyncExternalStore не перерисует сердечки. */
import { useSyncExternalStore } from 'react';
import { api } from './api.js';

let ids = new Set();
let loaded = false;
const listeners = new Set();
const emit = () => { ids = new Set(ids); for (const fn of listeners) fn(); };

export function loadLikes() {
  if (loaded) return Promise.resolve();
  return api('/api/likes')
    .then(d => { ids = new Set(d.ids || []); loaded = true; emit(); })
    .catch(() => { /* без лайков тоже живём */ });
}

function sub(fn) { listeners.add(fn); return () => listeners.delete(fn); }

export function useLikes() {
  return useSyncExternalStore(sub, () => ids);
}

export async function toggleLike(id) {
  const was = ids.has(id);
  if (was) ids.delete(id); else ids.add(id);
  emit();
  try {
    const j = await api('/api/likes/' + id, { method: 'POST' });
    if (typeof j.liked === 'boolean') {
      if (j.liked) ids.add(id); else ids.delete(id);
      emit();
    }
  } catch {
    if (was) ids.add(id); else ids.delete(id);   // откат при сбое сети
    emit();
  }
  return !was;
}
