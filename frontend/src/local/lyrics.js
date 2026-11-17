/* LYRICS ENGINE §11: LRC только от пользователя (импорт/вставка).
   Никаких внешних текстов не тянем: права на тексты песен не наши.
   Всё хранится локально (track_lyrics, Dexie) — играет офлайн вместе
   с сохранённым треком. Разбор LRC по времени: [mm:ss.xx] (мульти-штампы,
   [offset:±ms]); без штампов — обычный текст (mode 'plain'). */
import { db } from './db.js';

/* --- разбор --- */

const TIME_RE = /\[(\d{1,2}):(\d{1,2}(?:[.:]\d{1,3})?)\]/g;

export function parseLRC(raw) {
  const text = String(raw || '').replace(/\r/g, '');
  let offsetMs = 0;
  const meta = {};
  const lines = [];
  let stamps = 0;
  for (const src of text.split('\n')) {
    if (!src.trim()) continue;
    const tag = /^\[(ti|ar|al|by|re|ve|length):(.*)\]$/i.exec(src.trim());
    if (tag) { meta[tag[1].toLowerCase()] = tag[2].trim(); continue; }
    const off = /^\[offset:\s*([+-]?\d+)\s*\]$/i.exec(src.trim());
    if (off) { offsetMs = parseInt(off[1], 10) || 0; continue; }
    /* ведущие штампы: [00:12.50][01:02] текст — все штампы на одно тело */
    TIME_RE.lastIndex = 0;
    let m, lastEnd = -1;
    const ts = [];
    while ((m = TIME_RE.exec(src))) {
      const min = parseInt(m[1], 10) || 0;
      const sec = parseFloat((m[2] || '0').replace(':', '.')) || 0;
      ts.push(min * 60 + sec);
      lastEnd = TIME_RE.lastIndex;
    }
    if (ts.length) {
      const body = src.slice(lastEnd).trim();
      if (body) { for (const t of ts) lines.push({ t, text: body }); stamps += ts.length; }
    } else {
      const plain = src.replace(/\[[^\]]*\]/g, '').trim();
      if (plain) lines.push({ t: null, text: plain });
    }
  }
  if (offsetMs && stamps) {
    const d = offsetMs / 1000;
    for (const l of lines) if (l.t != null) l.t = Math.max(0, l.t + d);
  }
  lines.sort((a, b) => (a.t ?? 1e12) - (b.t ?? 1e12));
  const mode = stamps > 0 ? 'lrc' : 'plain';
  return { mode, offsetMs, meta,
           lines: mode === 'lrc' ? lines : lines.map(l => ({ t: null, text: l.text })) };
}

/* Текущая строка синхронного текста: последняя с t <= sec.
   До первой строки → -1. */
export function currentLine(lines, sec) {
  if (!lines?.length) return -1;
  let lo = 0, hi = lines.length - 1, res = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].t != null && lines[mid].t <= sec) { res = mid; lo = mid + 1; }
    else hi = mid - 1;
  }
  return res;
}

/* --- хранение (локальное, per-device) --- */

export async function getLyrics(trackId) {
  if (!trackId) return null;
  try {
    const row = await db.track_lyrics.get(trackId);
    if (!row) return null;
    const parsed = parseLRC(row.text);
    return { ...parsed, raw: row.text, updated: row.updated };
  } catch {
    return null;
  }
}

export async function saveLyrics(trackId, text) {
  if (!trackId) throw new Error('нет трека');
  const clean = String(text || '').slice(0, 200000);      // ~200 кБ с запасом
  if (!clean.trim()) throw new Error('пустой текст');
  await db.track_lyrics.put({ track_id: trackId, text: clean, updated: Date.now() });
  return parseLRC(clean);
}

export async function removeLyrics(trackId) {
  try { await db.track_lyrics.delete(trackId); } catch { /* ок */ }
}

export async function lyricsCount() {
  try { return await db.track_lyrics.count(); } catch { return 0; }
}
