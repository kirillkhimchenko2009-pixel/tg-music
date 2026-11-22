/* HTTP-клиент v2: initData (внутри Telegram) → Bearer (тихий гость в браузере). */
import { authToken, initDataRaw, setAuthToken } from './store.js';
import { classifyDevice } from './device.js';

export const API_BASE = (new URLSearchParams(location.search).get('api') || '')
  .replace(/\/$/, '');

export function headers() {
  const h = { 'Content-Type': 'application/json' };
  if (initDataRaw.value) h['X-Init-Data'] = initDataRaw.value;
  if (!initDataRaw.value && authToken.value) h['Authorization'] = 'Bearer ' + authToken.value;
  h['X-Pinggy-No-Screen'] = '1';
  h['ngrok-skip-browser-warning'] = '1';
  return h;
}

export function hasAuth() {
  return !!(initDataRaw.value || authToken.value);
}

/* Гость — тихая сессия браузера: слушать можно, но отправить файл в чат
   нельзя (это не Telegram chat_id). Для этого нужен вход по коду. */
export function isGuest() {
  if (initDataRaw.value) return false;
  const uid = parseInt(String(authToken.value).split('.')[0], 10);
  return Number.isFinite(uid) && uid >= 1e12;
}

/* Бот, у которого можно получить /code. */
export const BOT_URL = 'https://t.me/tG_dols_music_bot?start=app';

/* Одноразовый код из /code → Bearer настоящего Telegram-пользователя. */
export async function exchangeCode(code) {
  const res = await fetch(API_BASE + '/api/auth/exchange', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Pinggy-No-Screen': '1' },
    body: JSON.stringify({ code }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(j.detail || 'код не подошёл');
  setAuthToken(j.token);
  // Имя для топбара: @username, иначе нейтральное «Telegram».
  localStorage.setItem('display_name',
    j.user?.username ? '@' + j.user.username : 'Telegram');
  return j;
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
      if (e?.name === 'AbortError') throw e;
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
      if (msg === 'telegram_required') window.dispatchEvent(new Event('vibe:login'));
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

/* Устройство — косвенный признак для контекста (без геолокации и без
   категоричных выводов на сервере: сервер валидирует и может игнорировать). */
export function deviceKind() {
  return classifyDevice({
    userAgent: navigator.userAgent || '',
    width: Math.min(window.innerWidth || 0, window.innerHeight || 0),
    touch: 'ontouchstart' in window,
    touchPoints: navigator.maxTouchPoints || 0,
  });
}

/* Контекст запроса: локальное время пользователя (не сервера) + устройство.
   Всё опционально: сервер валидирует ключи и уважает context_enabled=false. */
export function waveCtx(extra = {}) {
  const d = new Date();
  return {
    local_hour: d.getHours(),
    local_weekday: (d.getDay() === 0 ? 7 : d.getDay()),
    device: deviceKind(),
    ...extra,
  };
}

export function waveStart(engine, ctx, opts = {}) {
  return api('/api/wave/start', { method: 'POST', signal: opts.signal,
    body: JSON.stringify({ engine: engine || null, ...(ctx || {}) }) });
}

export function waveNext(sessionId, last, ctx) {
  return api('/api/wave/next', { method: 'POST',
    body: JSON.stringify({ session_id: sessionId, last: last || null, ...(ctx || {}) }) });
}

export function waveFeedback(sessionId, event, trackId, playedSeconds, extra = {}) {
  return api('/api/wave/feedback', { method: 'POST', body: JSON.stringify({
    session_id: sessionId, event,
    track_id: trackId ?? undefined,
    played_seconds: playedSeconds ?? undefined,
    ...extra }) });
}

/* Гранулярные события плеера (§7.1): метки прослушивания, seek, replay.
   Плюс явные события: disliked (с reason), added_to_library и т.п.
   Fire-and-forget: телеметрия не должна ронять UI. */
export function waveEvent(sessionId, kind, opts = {}) {
  const d = new Date();
  return api('/api/wave/event', { method: 'POST', body: JSON.stringify({
    session_id: sessionId ?? undefined,
    kind,
    track_id: opts.trackId ?? undefined,
    played_seconds: opts.playedSeconds ?? undefined,
    duration: opts.duration ?? undefined,
    reason: opts.reason ?? undefined,
    scenario: opts.scenario ?? undefined,
    local_hour: d.getHours(),
    local_weekday: (d.getDay() === 0 ? 7 : d.getDay()),
    device: deviceKind(),
    activity: opts.activity ?? undefined,
    mood: opts.mood ?? undefined,
  }) }).catch(() => {});
}

/* Карточки сценариев (динамические по профилю и контексту). */
export function waveScenarios(ctx, opts = {}) {
  const q = new URLSearchParams();
  if (ctx?.local_hour != null) q.set('local_hour', String(ctx.local_hour));
  if (ctx?.local_weekday != null) q.set('local_weekday', String(ctx.local_weekday));
  if (ctx?.device) q.set('device', ctx.device);
  const qs = q.toString();
  return api('/api/wave/scenarios' + (qs ? '?' + qs : ''), { signal: opts.signal });
}

/* «Встряхнуть волну»: альтернативный сценарий, вкус не обнуляется. */
export function waveShake(sessionId) {
  return api('/api/wave/shake', { method: 'POST',
    body: JSON.stringify({ session_id: sessionId, ...waveCtx() }) });
}

/* Профиль вкуса и исключения (§12, §20). */
export function waveTaste() { return api('/api/wave/taste'); }
export function waveTasteExclude(patch) {
  return api('/api/wave/taste/exclude', { method: 'POST', body: JSON.stringify(patch) });
}
export function waveTasteInclude(patch) {
  return api('/api/wave/taste/include', { method: 'POST', body: JSON.stringify(patch) });
}

/* Приватность: очистка истории/вкуса. */
export function waveClear(scope) {
  return api('/api/wave/clear', { method: 'POST', body: JSON.stringify({ scope }) });
}

/* Метрики волны (§17). */
export function waveMetrics(days = 14) {
  return api('/api/wave/metrics?days=' + encodeURIComponent(String(days)));
}

/* Карточки новых артистов (§9.1). */
export function waveDiscovery(limit = 8) {
  return api('/api/wave/discovery?limit=' + encodeURIComponent(String(limit)));
}

export function waveSettings() { return api('/api/wave/settings'); }

export function waveSaveSettings(patch) {
  return api('/api/wave/settings', { method: 'PUT', body: JSON.stringify(patch) });
}
