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

/* «Всё в любимое»: пачка треков. Сервер режет пачку до
   WEBAPP_LIKES_BULK_MAX (по умолчанию 500) — «Мне нравится» на 1241
   переносился только до 500-го, остальное молча отбрасывалось. Теперь
   шлём чанками по CHUNK последовательно (параллельные INSERT-ы на Neon
   плодят «database is locked»). Оптимистично помечаем; сервер вернёт
   только реально добавленные (existing = уже были) — снимаем лишь те,
   что он молча пропустил (нет в каталоге). */
const BULK_CHUNK = 500;   // = дефолт WEBAPP_LIKES_BULK_MAX на бэкенде

export async function addLikesBulk(list) {
  const want = [...new Set(list || [])];
  if (!loaded) { try { await loadLikes(); } catch { /* живём и так */ } }
  const fresh = want.filter(id => !ids.has(id));
  if (!fresh.length) return { added: 0, ids: [], existing: want };
  fresh.forEach(id => ids.add(id));
  emit();
  const freshSet = new Set(fresh);
  const accepted = new Set();                 // подтверждено сервером
  const total = { added: 0, ids: [], existing: [] };
  try {
    for (let i = 0; i < want.length; i += BULK_CHUNK) {
      const part = want.slice(i, i + BULK_CHUNK);
      const j = await api('/api/likes/bulk', {
        method: 'POST',
        body: JSON.stringify({ track_ids: part }),
      });
      const got = new Set(j.ids || []);
      const had = new Set(j.existing || []);
      for (const id of part) {
        if (!freshSet.has(id)) continue;
        if (!got.has(id) && !had.has(id)) ids.delete(id);
        else accepted.add(id);
      }
      total.added += j.added || 0;
      total.ids.push(...(j.ids || []));
      total.existing.push(...(j.existing || []));
      emit();
    }
    return total;
  } catch {
    /* откат при сбое: снимаем только неподтверждённое — прошлые чанки
       сервер уже закоммичил, их пометку оставляем */
    for (const id of fresh) if (!accepted.has(id)) ids.delete(id);
    emit();
    throw new Error('Не удалось добавить в любимое');
  }
}
