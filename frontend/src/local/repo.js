/* REPOSITORY LAYER §3 (слой C): все изменения локального состояния —
   только отсюда. Тяжёлых операций внутри IndexedDB-транзакций нет:
   сеть/хеширование/декод выполняются ДО входа в транзакцию. */
import { db } from './db.js';
import { API_BASE, api } from '../api.js';

const now = () => Date.now();

/* ---------- кеш метаданных (remote library → local snapshot) ---------- */

export async function cacheTracks(list) {
  const rows = (list || []).filter(t => t && t.id).map(t => ({
    id: t.id,
    title: t.title || '',
    artist: t.artist || '',
    album: t.album || '',
    duration: t.duration || 0,
    cover_url: t.cover_url || null,
    ready: !!t.ready,
    audio_url: t.audio_url || null,
    source: t.source || '',
    podcast: !!t.podcast,
    artist_id: t.artist_id ?? null,
    album_id: t.album_id ?? null,
    dl_state: t.dl_state || 'remote',
    hash: t.hash || null,
    size: t.size || 0,
    date_added: t.date_added || now(),
    last_played: t.last_played || 0,
  }));
  if (!rows.length) return;
  await db.transaction('rw', db.tracks, async () => {
    for (const r of rows) {
      const prev = await db.tracks.get(r.id);
      if (prev) {
        // не затираем локальные факты скачивания/хеша серверным снапшотом
        await db.tracks.update(r.id, {
          title: r.title, artist: r.artist, album: r.album,
          duration: r.duration, cover_url: r.cover_url, ready: r.ready,
          audio_url: r.audio_url || prev.audio_url || null,
          podcast: r.podcast || prev.podcast || false,
          source: r.source, artist_id: r.artist_id, album_id: r.album_id,
        });
      } else {
        await db.tracks.put(r);
      }
    }
  });
}

export async function getTrack(id) {
  return db.tracks.get(id) || null;
}

export async function getTracks(ids) {
  const out = [];
  for (const id of ids || []) {
    const t = await db.tracks.get(id);
    if (t) out.push(t);
  }
  return out;
}

/* Локальный источник для плеера: blob URL файла или null (нет/сломан). */
export async function localSrc(trackId) {
  try {
    const t = await db.tracks.get(trackId);
    if (!t || t.dl_state !== 'local' || !t.hash) return null;
    const { getAudioFile } = await import('./storage.js');
    const f = await getAudioFile(t.hash);
    if (!f) {
      await markMissing(trackId);        // файл исчез из хранилища §10
      return null;
    }
    return URL.createObjectURL(f);
  } catch {
    return null;
  }
}

export async function markLocal(trackId, hash, size) {
  await db.transaction('rw', db.tracks, db.storage_manifest, async () => {
    await db.tracks.update(trackId, { dl_state: 'local', hash, size: size || 0 });
    await db.storage_manifest.put({ hash, track_id: trackId, size: size || 0,
                                    created: now() });
  });
  _offlineIds.add(trackId);
  _notifyOffline();
}

export async function markMissing(trackId) {
  try { await db.tracks.update(trackId, { dl_state: 'missing' }); } catch { /* ок */ }
  _offlineIds.delete(trackId);
  _notifyOffline();
}

export async function removeLocal(trackId) {
  await db.transaction('rw', db.tracks, db.storage_manifest,
                       db.download_jobs, async () => {
    const t = await db.tracks.get(trackId);
    if (t?.hash) {
      const others = await db.tracks.where('hash').equals(t.hash)
        .and(x => x.id !== trackId).count();
      if (!others) {
        const { deleteAudio } = await import('./storage.js');
        await deleteAudio(t.hash);
        await db.storage_manifest.delete(t.hash);
      }
      await db.tracks.update(trackId, { dl_state: 'remote', hash: null, size: 0 });
    }
    await db.download_jobs.delete(trackId);
  });
  _offlineIds.delete(trackId);
  _notifyOffline();
}

export async function offlineTracks() {
  return db.tracks.where('dl_state').equals('local').toArray();
}

/* ---------- реактивный набор офлайн-треков (бейдж в TrackRow, чип плейлиста) ---------- */
let _offlineIds = new Set();
const _offListeners = new Set();

export function subscribeOffline(fn) {
  _offListeners.add(fn);
  return () => _offListeners.delete(fn);
}

export function isOffline(id) {
  return _offlineIds.has(id);
}

function _notifyOffline() {
  for (const fn of _offListeners) fn();
}

export async function loadOfflineIds() {
  try {
    const rows = await db.tracks.where('dl_state').equals('local').toArray();
    _offlineIds = new Set(rows.map(r => r.id));
    _notifyOffline();
  } catch { /* ок */ }
}

/* Сколько из перечисленных треков уже сохранено офлайн (чип на плейлисте). */
export async function localCount(ids) {
  const clean = (ids || []).filter(i => Number.isFinite(i));
  if (!clean.length) return 0;
  try {
    return await db.tracks.where('id').anyOf(clean)
      .and(t => t.dl_state === 'local').count();
  } catch { return 0; }
}

/* ---------- настройки §5/§19 ---------- */

export async function getSetting(key, dflt = null) {
  const row = await db.user_settings.get(key);
  return row ? row.value : dflt;
}

export async function setSetting(key, value) {
  await db.user_settings.put({ key, value, at: now() });
}

/* ---------- история (локальная) ---------- */

export async function addHistory(trackId, { finished = false, skipped = false,
                                          played = 0, local = null } = {}) {
  try {
    if (!Number.isSafeInteger(Number(trackId))) return;
    await db.playback_history.add({
      track_id: trackId, at: now(), finished: !!finished,
      skipped: !!skipped, played: Math.max(0, Math.round(played || 0)),
      origin: local === true ? 'local' : 'web',
    });
    if (trackId) await db.tracks.update(trackId, { last_played: now() });
  } catch { /* история не должна ломать плеер */ }
}

/* ---------- офлайн-события §17 (идемпотентно по event_id) ---------- */

function uuid() {
  try { return crypto.randomUUID(); }
  catch {
    return 'e' + now().toString(36) + Math.random().toString(36).slice(2, 10);
  }
}

export async function recordEvent(ev) {
  if (ev.track_id != null && !Number.isSafeInteger(Number(ev.track_id))) return null;
  const row = {
    event_id: uuid(),
    kind: ev.kind || 'started',
    track_id: ev.track_id ?? null,
    played_seconds: Math.max(0, Math.round(ev.played_seconds || 0)),
    finished: !!ev.finished,
    skipped: !!ev.skipped,
    at: now(),
    state: 'pending',
  };
  await db.offline_events.put(row);
  scheduleSync();
  return row;
}

let syncTimer = null;

export function scheduleSync() {
  if (typeof window === 'undefined') return;
  if (syncTimer) return;
  syncTimer = setTimeout(() => { syncTimer = null; syncEvents().catch(() => {}); }, 2500);
}

export async function pendingEvents(limit = 200) {
  return db.offline_events.where('state').equals('pending').limit(limit).toArray();
}

/* Отправка пачки на сервер; сервер дедуплицирует по (user_id, event_id).
   Повторной отправки уже закемиченных нет: rows удаляются после ack. */
export async function syncEvents() {
  const rows = await pendingEvents(200);
  if (!rows.length) return 0;
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return 0;
  try {
    const j = await api('/api/history/sync', { method: 'POST', body: JSON.stringify({
      events: rows.map(r => ({
        event_id: r.event_id,
        track_id: r.podcast ? undefined : (r.track_id ?? undefined),
        kind: r.kind,
        played_seconds: r.played_seconds,
        finished: r.finished,
        skipped: r.skipped,
        at: r.at,
      })),
    }) });
    const acked = new Set(j.acked || []);
    await db.offline_events.bulkDelete(rows.filter(r => acked.has(r.event_id))
                                       .map(r => r.event_id));
    return acked.size;
  } catch {
    return 0;                            // останутся pending — уйдут позже
  }
}

/* ---------- восстановление очереди §10 ---------- */

export async function resolveQueue(ids) {
  const uniq = [...new Set(ids || [])].filter(i => Number.isFinite(i));
  if (!uniq.length) return [];
  const cached = await getTracks(uniq);
  const have = new Set(cached.map(t => t.id));
  const missing = uniq.filter(id => !have.has(id));
  let remote = [];
  if (missing.length && (typeof navigator === 'undefined' || navigator.onLine !== false)) {
    try {
      const j = await api('/api/tracks/batch', { method: 'POST',
        body: JSON.stringify({ ids: missing }) });
      remote = j.tracks || [];
      await cacheTracks(remote);
    } catch { /* оффлайн — обойдёмся кешем */ }
  }
  const byId = new Map([...cached, ...remote].map(t => [t.id, t]));
  return uniq.map(id => byId.get(id)).filter(Boolean).map(t => ({
    ...t, cover_url: t.cover_url && !/^https?:/.test(t.cover_url)
      ? API_BASE + t.cover_url : t.cover_url,
  }));
}

/* слушатели: синк при возвращении сети + первичная загрузка офлайн-набора */
if (typeof window !== 'undefined') {
  window.addEventListener('online', () => scheduleSync());
  loadOfflineIds();
}
