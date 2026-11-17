/* RESUMABLE DOWNLOAD MANAGER §5 (по мотивам Crate — MIT, код не копировался).
   Приоритетная очередь, ≤2 параллельных, возобновление через HTTP Range
   (только если сервер реально поддержал: 206 + согласованность total),
   отмена/ретрай, проверка целостности (размер + SHA-256 + магия формата),
   дедуп по хешу (§6): одинаковые файлы не хранятся дважды.
   Не обещаем фоновых загрузок, когда WebView убит системой: недокачанные
   джобы дожимаются при следующем открытии приложения. */
import { API_BASE, api } from '../api.js';
import * as storage from './storage.js';
import { hashBlob } from './hash.js';
import { markLocal, removeLocal, setSetting, getSetting } from './repo.js';
import { db } from './db.js';

const CONCURRENCY = 2;
const FLUSH_EVERY = 2 * 1024 * 1024;         // слив недокачанной части каждые 2 МиБ
const MIN_PARTIAL = 128 * 1024;              // меньше — не хранить часть вовсе

const jobs = new Map();                      // trackId → view
const listeners = new Set();
let running = 0;
let booted = false;

export function subscribeDl(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function view(trackId) {
  let v = jobs.get(trackId);
  if (!v) {
    v = { trackId, state: 'queued', received: 0, total: 0, name: '', error: null,
          priority: 0, hash: null };
    jobs.set(trackId, v);
  }
  return v;
}

function emit() {
  const snap = [...jobs.values()].map(v => ({ ...v, ctrl: undefined }));
  for (const fn of listeners) fn(snap);
}

export function jobState(trackId) {
  return jobs.get(trackId) || null;
}

/* --- Wi-Fi-гейт §5: navigator.connection — только дополнительный сигнал --- */
export async function wifiOnly() {
  return !!(await getSetting('dl_wifi_only', false));
}

export async function setWifiOnly(v) {
  await setSetting('dl_wifi_only', !!v);
}

async function wifiGate() {
  if (!(await wifiOnly())) return;
  const c = typeof navigator !== 'undefined' ? navigator.connection : null;
  const type = c && (c.type || (c.effectiveType ? 'cellular?' : undefined));
  if (c && c.saveData) throw new Error('Включён режим экономии трафика');
  if (c && c.type === 'cellular') throw new Error('Только по Wi-Fi: сейчас мобильная сеть');
  if (type === undefined && !c) return;    // iOS: Connection API нет — не блокируем
  // wifi/ethernet/unknown → можно
}

/* ---------------- публичное API ---------------- */

export async function enqueue(track, priority = 1) {
  if (!booted) await boot();
  if (!track?.id) throw new Error('нет трека');
  const st = await storage.storageInfo();
  if (st.strategy === 'none') throw new Error('Хранилище недоступно');
  const prev = await db.tracks.get(track.id);
  if (prev?.dl_state === 'local' && prev.hash && await storage.hasAudio(prev.hash)) {
    return 'done';
  }
  await wifiGate();
  const v = view(track.id);
  v.name = track.title ? `${track.artist || ''} — ${track.title}`.replace(/^ — /, '') : '';
  v.priority = priority;
  v.error = null;
  if (v.state === 'done') v.state = 'queued';
  await db.download_jobs.put({ track_id: track.id, state: v.state === 'down' ? 'down' : 'queued',
                               created: Date.now(), priority, name: v.name });
  emit();
  pump();
  return 'queued';
}

export async function cancel(trackId) {
  const v = jobs.get(trackId);
  if (v) {
    v.cancelled = true;
    try { v.ctrl?.abort(); } catch { /* ок */ }
    if (v.state === 'queued') { v.state = 'paused'; emit(); }
  }
  await db.download_jobs.delete(trackId);
}

export async function retry(trackId) {
  const row = await db.tracks.get(trackId);
  return enqueue({ id: trackId, title: row?.title || '', artist: row?.artist || '' }, 2);
}

export async function removeOffline(trackId) {
  await removeLocal(trackId);
  const v = jobs.get(trackId);
  if (v) { v.state = 'remote'; v.hash = null; }
  emit();
}

/* Перечень сохранённых офлайн (для экрана Хранилища). */
export async function offlineList() {
  const { offlineTracks } = await import('./repo.js');
  return offlineTracks();
}

/* ---------------- исполнитель очереди ---------------- */

function pump() {
  if (running >= CONCURRENCY) return;
  const ready = [...jobs.values()]
    .filter(v => v.state === 'queued')
    .sort((a, b) => b.priority - a.priority || a.trackId - b.trackId);
  const v = ready[0];
  if (!v) return;
  running += 1;
  run(v).finally(() => {
    running -= 1;
    pump();
  });
  if (running < CONCURRENCY) pump();       // вторая параллельная
}

async function run(v) {
  const trackId = v.trackId;
  const ctrl = new AbortController();
  v.ctrl = ctrl;
  v.state = 'down';
  v.error = null;
  emit();
  await db.download_jobs.put({ track_id: trackId, state: 'down',
                               created: Date.now(), priority: v.priority, name: v.name });
  try {
    /* 1) тот же подписанный URL, что у плеера (same-origin → fetch без CORS) */
    const j = await api('/api/player/' + trackId);
    if (j.kind === 'preview') {
      /* 30-секундный фрагмент нельзя выдавать за полный трек: честная
         ошибка — юзер сначала качает трек на сервер («Скачать в чат») */
      throw new Error('полный трек ещё не на сервере — сначала «Скачать в чат»');
    }
    const url = API_BASE + j.url;

    /* 2) недокачанная часть с прошлого раза */
    let head = null, headMeta = null;
    try {
      const p = await storage.getPartial(trackId);
      if (p?.blob?.size > MIN_PARTIAL) { head = p.blob; headMeta = p.meta; }
    } catch { /* нет части — старт с нуля */ }
    let received = head ? head.size : 0;
    let total = headMeta?.total || 0;

    /* 3) Range-запрос; 200 при наличии части = источник не поддержал
          докачку — старую часть выбрасываем, не склеиваем несовместимое */
    const headers = {};
    if (received > 0) headers.Range = 'bytes=' + received + '-';
    let resp = await fetch(url, { headers, signal: ctrl.signal, redirect: 'follow' });
    if (!resp.ok && resp.status !== 206) throw new Error('HTTP ' + resp.status);
    if (received > 0 && resp.status === 200) { head = null; received = 0; }
    if (resp.status === 206) {
      const cr = resp.headers.get('content-range') || '';
      const m = /\/(\d+)\s*$/.exec(cr);
      const crTotal = m ? +m[1] : 0;
      if (head && crTotal && total && crTotal !== total) {
        /* источник сменился (total другой) — не склеиваем несовместимое:
           старую часть выбрасываем и качаем заново целиком */
        try { await resp.body?.cancel(); } catch { /* ок */ }
        head = null; received = 0; total = crTotal;
        resp = await fetch(url, { signal: ctrl.signal, redirect: 'follow' });
        if (!resp.ok && resp.status !== 206) throw new Error('HTTP ' + resp.status);
      } else if (crTotal) {
        total = crTotal;
      }
    } else {
      const cl = +resp.headers.get('content-length') || 0;
      if (cl) total = cl;
    }
    v.total = total;
    emit();

    /* 4) чтение тела; части копим в памяти (лимит файлов ~20 МиБ) и
          периодически сливаем в постоянное хранилище для возобновления */
    const chunks = head ? [head] : [];
    received = head ? head.size : 0;
    let sinceFlush = 0;
    if (resp.body && typeof resp.body.getReader === 'function') {
      const reader = resp.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        received += value.byteLength;
        sinceFlush += value.byteLength;
        v.received = received;
        emit();
        if (sinceFlush >= FLUSH_EVERY) {
          sinceFlush = 0;
          try { await storage.savePartial(trackId, new Blob(chunks), { total }); }
          catch { /* слив части не критичен */ }
        }
      }
    } else {
      const ab = await resp.arrayBuffer();   // старый WebView: одним куском
      chunks.push(ab);
      received = ab.byteLength;
    }
    v.received = received;
    if (total && received !== total) throw new Error('файл не докачался');

    /* 5) целостность §5/§6: размер, магия формата, SHA-256 */
    const blob = new Blob(chunks);
    if (blob.size < 2048) throw new Error('файл слишком мал');
    const magic = new Uint8Array(await blob.slice(0, 16).arrayBuffer());
    if (!looksLikeAudio(magic)) throw new Error('формат не распознан');
    v.state = 'hash';
    emit();
    const hash = await hashBlob(blob);

    /* 6) постоянное хранилище + дедуп: файл с тем же хешем не дублируем */
    const existed = await storage.hasAudio(hash);
    if (!existed) await storage.saveAudio(hash, blob);
    else await storage.clearPartial(trackId).catch(() => {});
    await markLocal(trackId, hash, blob.size);
    await db.download_jobs.delete(trackId);
    try { await storage.clearPartial(trackId); } catch { /* ок */ }

    v.state = 'done';
    v.hash = hash;
    v.received = blob.size;
    v.total = blob.size;
    emit();
  } catch (e) {
    const aborted = e && (e.name === 'AbortError' || e.code === 20);
    if (aborted) {
      v.state = 'paused';                  // отмена: часть сохранена, можно докачать
      if (v.cancelled) {
        v.cancelled = false;
        await db.download_jobs.delete(trackId);
      } else {
        await db.download_jobs.put({ track_id: trackId, state: 'paused',
                                     created: Date.now(), priority: v.priority,
                                     name: v.name });
      }
    } else {
      v.state = 'error';
      v.error = (e && e.message) || 'ошибка загрузки';
      await db.download_jobs.put({ track_id: trackId, state: 'error',
                                   created: Date.now(), priority: v.priority,
                                   name: v.name, error: v.error });
    }
    emit();
  }
}

/* Магия формата: mp3 (ID3 / кадр), m4a (ftyp), flac, ogg/opus, wav, aac. */
function looksLikeAudio(b) {
  if (b.length < 8) return false;
  const s = (o, str) => [...str].every((c, i) => b[o + i] === c.charCodeAt(0));
  if (s(0, 'ID3')) return true;
  if (s(4, 'ftyp')) return true;
  if (s(0, 'fLaC')) return true;
  if (s(0, 'OggS')) return true;
  if (s(0, 'RIFF')) return true;
  if (b[0] === 0xff && (b[1] & 0xe0) === 0xe0) return true;   // MPEG frame sync
  if (b[0] === 0xff && b[1] === 0xf1) return true;            // ADTS
  if (s(0, '<?xml') || s(0, '<!DOCT') || s(0, '{')) return false; // страница ошибки
  return true;                            // незнакомый бинарник — пусть решает декодер
}

/* ---------------- возобновление при открытии приложения ---------------- */

export async function boot() {
  if (booted) return;
  booted = true;
  try {
    const rows = await db.download_jobs.toArray();
    for (const r of rows) {
      if (r.state === 'done') { await db.download_jobs.delete(r.track_id); continue; }
      const t = await db.tracks.get(r.track_id);
      const v = view(r.track_id);
      v.name = r.name || (t ? `${t.artist || ''} — ${t.title}`.replace(/^ — /, '') : '');
      v.priority = 0;                    // дозагрузка фоном, ниже пользовательских
      if (r.state === 'error') { v.state = 'error'; v.error = r.error || 'ошибка'; }
      else { v.state = 'queued'; }
      emit();
      pump();
    }
  } catch { /* слой загрузок не должен ронять приложение */ }
}
