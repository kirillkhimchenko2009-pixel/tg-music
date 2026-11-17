/* RECOVER PLAYBACK §10: сохранение сессии с debounce (не каждый timeupdate)
   и восстановление: last track, позиция, очередь (id), shuffle/repeat,
   «Продолжить прослушивание». Файл исчез → честно помечаем missing. */
import { getSetting, setSetting, resolveQueue } from './repo.js';

const KEY = 'session';
const SAVE_INTERVAL = 5000;              // не чаще, чем раз в 5 с по позиции

let _last = 0;
let _timer = null;
let _latest = null;

function row(obj) {
  return { ...obj, at: Date.now() };
}

/* Немедленное сохранение (play/pause/ended/скрытие вкладки). */
export function saveSession(sess) {
  _latest = row(sess);
  return setSetting(KEY, _latest).catch(() => { /* ок */ });
}

/* Debounced-вариант для timeupdate. */
export function saveSessionSoon(sess) {
  _latest = row(sess);
  const t = Date.now();
  if (t - _last >= SAVE_INTERVAL) {
    _last = t;
    setSetting(KEY, _latest).catch(() => {});
    return;
  }
  if (_timer) return;
  _timer = setTimeout(() => {
    _timer = null;
    _last = Date.now();
    if (_latest) setSetting(KEY, _latest).catch(() => {});
  }, Math.max(500, SAVE_INTERVAL - (Date.now() - _last)));
}

export async function getSession(maxAgeDays = 14) {
  try {
    const s = await getSetting(KEY);
    if (!s || !s.t) return null;
    if (maxAgeDays && s.at && Date.now() - s.at > maxAgeDays * 864e5) return null;
    return s;
  } catch {
    return null;
  }
}

export async function clearSession() {
  try { await setSetting(KEY, null); } catch { /* ок */ }
}

/* Восстановление: id → объекты треков (кеш → сервер), пропавшие — мимо.
   Возвращает { tracks, index, pos } или null. */
export async function restoreSession() {
  const s = await getSession();
  if (!s) return null;
  const tracks = await resolveQueue(s.q || []);
  if (!tracks.length) return null;
  const ids = new Set(tracks.map(t => t.id));
  if (!ids.has(s.t)) {
    // текущий трек недоступен локально/на сервере — начинаем с доступного
    const idx = Math.max(0, Math.min(s.i | 0, tracks.length - 1));
    return { tracks, index: idx, pos: 0, shuffle: !!s.sh, loop: !!s.lo, volume: s.v };
  }
  const index = Math.max(0, tracks.findIndex(t => t.id === s.t));
  const tr = tracks[index];
  const pos = (s.p | 0) < (tr.duration || 0) - 2 ? (s.p | 0) : 0;
  return { tracks, index, pos, shuffle: !!s.sh, loop: !!s.lo, volume: s.v };
}
