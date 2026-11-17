/* ЛОКАЛЬНОЕ ХРАНИЛИЩЕ АУДИОФАЙЛОВ §4 (слой B).
   Стратегия выбирается один раз по feature detection:
   1) OPFS (navigator.storage.getDirectory + createWritable) — основная;
   2) IndexedDB Blob (Dexie-таблицы audio_files/audio_tmp) — фолбэк для
      WebView без writable-OPFS (в т.ч. старые iOS WKWebView).
   Двойного сохранения нет: одна стратегия на устройство. Персональные
   аудио ни в каком общем SW-кэше не живут (§18). */
import { db } from './db.js';

let _info = null;

async function _probe() {
  const res = { strategy: 'idb', opfs: false, writable: false, streamBody: true };
  try {
    if (typeof navigator !== 'undefined' && navigator.storage?.getDirectory) {
      const root = await navigator.storage.getDirectory();
      res.opfs = true;
      try {
        const fh = await root.getFileHandle('.vibe-probe', { create: true });
        const w = await fh.createWritable();
        await w.write(new Uint8Array([1]));
        await w.close();
        try { await root.removeEntry('.vibe-probe'); } catch { /* уже нет */ }
        res.writable = true;
      } catch { res.writable = false; }
    }
  } catch { res.opfs = false; }
  if (res.opfs && res.writable) res.strategy = 'opfs';
  return res;
}

export function storageInfo() {
  if (!_info) _info = _probe();
  return _info;
}

export async function requestPersist() {
  try {
    if (navigator.storage?.persist) return !!(await navigator.storage.persist());
  } catch { /* not supported */ }
  return false;
}

export async function isPersisted() {
  try { return !!(navigator.storage?.persisted && await navigator.storage.persisted()); }
  catch { return false; }
}

/* Квота/использование §19: значения честные « best-effort», могут быть null. */
export async function estimate() {
  try { return await navigator.storage.estimate() || {}; }
  catch { return {}; }
}

async function opfsDir(name) {
  const root = await navigator.storage.getDirectory();
  return root.getDirectoryHandle(name, { create: true });
}

/* ---------- аудио (ключ — SHA-256) ---------- */

export async function saveAudio(hash, blob) {
  const st = await storageInfo();
  if (st.strategy === 'opfs') {
    const d = await opfsDir('audio');
    const fh = await d.getFileHandle(hash + '.bin', { create: true });
    const w = await fh.createWritable();
    try { await w.write(blob); } finally { await w.close(); }
    return 'opfs';
  }
  await db.audio_files.put({ key: hash, blob, size: blob.size, created: Date.now() });
  return 'idb';
}

export async function hasAudio(hash) {
  const st = await storageInfo();
  if (st.strategy === 'opfs') {
    try {
      const d = await opfsDir('audio');
      const fh = await d.getFileHandle(hash + '.bin');
      await fh.getFile();
      return true;
    } catch { return false; }
  }
  return !!(await db.audio_files.where('key').equals(hash).count());
}

export async function getAudioFile(hash) {
  const st = await storageInfo();
  if (st.strategy === 'opfs') {
    try {
      const d = await opfsDir('audio');
      const fh = await d.getFileHandle(hash + '.bin');
      return await fh.getFile();
    } catch { return null; }
  }
  const row = await db.audio_files.get(hash);
  return row?.blob || null;
}

export async function deleteAudio(hash) {
  const st = await storageInfo();
  if (st.strategy === 'opfs') {
    try {
      const d = await opfsDir('audio');
      await d.removeEntry(hash + '.bin');
    } catch { /* нет файла — ок */ }
    return;
  }
  await db.audio_files.delete(hash);
}

/* Ключи всех физических файлов — для проверки сирот §15. На OPFS нужен
   entries()/keys(); в старых WebView его нет → вернём null (проверка
   сирот просто не выполняется, честно сообщаем). */
export async function listAudioKeys() {
  const st = await storageInfo();
  if (st.strategy !== 'opfs') {
    return db.audio_files.toCollection().primaryKeys();
  }
  try {
    const d = await opfsDir('audio');
    const keys = [];
    if (d.keys) {
      for await (const name of d.keys()) {
        if (name.endsWith('.bin')) keys.push(name.slice(0, -4));
      }
    } else if (d.entries) {
      for await (const [name] of d.entries()) {
        if (name.endsWith('.bin')) keys.push(name.slice(0, -4));
      }
    } else {
      return null;                       // нет итерации каталога
    }
    return keys;
  } catch {
    return null;
  }
}

export async function clearAudio() {
  const st = await storageInfo();
  if (st.strategy !== 'opfs') {
    await db.audio_files.clear();
    return;
  }
  try {
    const d = await opfsDir('audio');
    const names = [];
    if (d.keys) { for await (const n of d.keys()) names.push(n); }
    else if (d.entries) { for await (const [n] of d.entries()) names.push(n); }
    for (const n of names) { try { await d.removeEntry(n); } catch { /* гонка ок */ } }
  } catch { /* каталога нет — ок */ }
}

/* ---------- недокачанные части (возобновление §5) ---------- */

export async function savePartial(trackId, blob, meta) {
  const st = await storageInfo();
  if (st.strategy === 'opfs') {
    const d = await opfsDir('tmp');
    const fh = await d.getFileHandle('p' + trackId + '.part', { create: true });
    const w = await fh.createWritable();
    try { await w.write(blob); } finally { await w.close(); }
    return;
  }
  await db.audio_tmp.put({ key: String(trackId), blob, meta, at: Date.now() });
}

export async function getPartial(trackId) {
  const st = await storageInfo();
  if (st.strategy === 'opfs') {
    try {
      const d = await opfsDir('tmp');
      const fh = await d.getFileHandle('p' + trackId + '.part');
      return { blob: await fh.getFile(), meta: null };
    } catch { return null; }
  }
  const row = await db.audio_tmp.get(String(trackId));
  return row ? { blob: row.blob, meta: row.meta || null } : null;
}

export async function clearPartial(trackId) {
  const st = await storageInfo();
  if (st.strategy === 'opfs') {
    try {
      const d = await opfsDir('tmp');
      await d.removeEntry('p' + trackId + '.part');
    } catch { /* нет — ок */ }
    return;
  }
  await db.audio_tmp.delete(String(trackId));
}
