/* HTTP-клиент v2: initData (внутри Telegram) → Bearer (тихий гость в браузере). */
import { authToken, initDataRaw, setAuthToken } from './store.js';

export const API_BASE = (new URLSearchParams(location.search).get('api') || '')
  .replace(/\/$/, '');

export function headers() {
  const h = { 'Content-Type': 'application/json' };
  if (initDataRaw.value) h['X-Init-Data'] = initDataRaw.value;
  if (authToken.value) h['Authorization'] = 'Bearer ' + authToken.value;
  h['X-Pinggy-No-Screen'] = '1';
  h['ngrok-skip-browser-warning'] = '1';
  return h;
}

export function hasAuth() {
  return !!(initDataRaw.value || authToken.value);
}

let onUnauthorized = null;
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

const TRANSIENT = new Set([502, 520, 521, 522, 524, 526, 530]);

export async function api(path, opts = {}) {
  let lastErr;
  const retryable = !opts.method || opts.method === 'GET';
  const tries = retryable ? 3 : 1;
  for (let attempt = 0; attempt < tries; attempt++) {
    if (attempt) await new Promise(r => setTimeout(r, 1200 * attempt));
    let res;
    try {
      res = await fetch(API_BASE + path, { headers: headers(), ...opts });
    } catch (e) {
      lastErr = new Error('Нет связи с сервером: туннель перезапустился. Нажми 📱 «Открыть приложение» заново');
      continue;
    }
    if (retryable && TRANSIENT.has(res.status)) {
      lastErr = new Error('туннель моргнул — повтори через пару секунд');
      continue;
    }
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch { /* plain */ }
      if (res.status === 401 && onUnauthorized) onUnauthorized(msg);
      throw new Error(msg);
    }
    const ct = res.headers.get('content-type') || '';
    if (!ct.includes('json')) {
      throw new Error('Сессия приложения устарела: закрой экран и нажми 📱 «Открыть приложение»');
    }
    return res.json();
  }
  throw lastErr || new Error('нет связи с сервером');
}

/* Гость в обычном браузере (без Telegram): тихая сессия по device_id.
   Никаких кодов и экранов входа — просто работает. */
export async function ensureGuestToken() {
  if (authToken.value) return;
  let dev = localStorage.getItem('device_id');
  if (!dev) {
    dev = (crypto.randomUUID ? crypto.randomUUID()
           : 'd' + Date.now().toString(36) + Math.random().toString(36).slice(2))
      .toLowerCase();
    localStorage.setItem('device_id', dev);
  }
  const res = await fetch(API_BASE + '/api/auth/guest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Pinggy-No-Screen': '1' },
    body: JSON.stringify({ device_id: dev }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(j.detail || 'не удалось создать гостевую сессию');
  setAuthToken(j.token);
}

/* ---------- стрим: короткоживущий токен в URL → нативный потоковый <audio> ---------- */
export async function playerSrc(trackId) {
  const j = await api('/api/player/' + trackId);
  return { url: API_BASE + j.url, kind: j.kind || null };   // /api/stream/{id}?t=…&u=… — откроется и в <audio>
}

export function coverUrl(t) {
  if (!t || !t.cover_url) return null;
  const raw = t.cover_url;
  if (raw.startsWith('http')) return raw;
  return API_BASE + raw;
}

/* ---------- Волна (/api/wave): старт, партии, фидбек, формула ---------- */
export function waveStart(engine) {
  return api('/api/wave/start', { method: 'POST',
    body: JSON.stringify({ engine: engine || null }) });
}

export function waveNext(sessionId, last) {
  return api('/api/wave/next', { method: 'POST',
    body: JSON.stringify({ session_id: sessionId, last: last || null }) });
}

export function waveFeedback(sessionId, event, trackId, playedSeconds) {
  return api('/api/wave/feedback', { method: 'POST', body: JSON.stringify({
    session_id: sessionId, event,
    track_id: trackId ?? undefined,
    played_seconds: playedSeconds ?? undefined }) });
}

export function waveSettings() { return api('/api/wave/settings'); }

export function waveSaveSettings(patch) {
  return api('/api/wave/settings', { method: 'PUT', body: JSON.stringify(patch) });
}
