/* Инициализация Telegram Mini App.
   Синхронный контур (как в v1, он работал): window.Telegram.WebApp.initData
   и разбор #tgWebAppData из URL-фрагмента — оба выполняются ДО первого рендера.
   SDK-путь остаётся обогащением после, он never блокирует рендер. */
import { initDataRaw } from './store.js';

let initialized = false;

/** Достаёт initData: WebApp → #tgWebAppData (один раз декодируем). */
export function extractInitData() {
  try {
    const wa = window.Telegram?.WebApp;
    if (wa && wa.initData) return wa.initData;
  } catch { /* noop */ }
  try {
    const m = /tgWebAppData=([^&]+)/.exec(
      location.hash || location.search.replace('?', '#') || '');
    if (m) {
      // value Яндекс-initData сам URL-encoded один раз:
      //   tgWebAppData=user%3D%7B%22id%22%3A...%26hash%3D...
      const dec = decodeURIComponent(m[1]);
      if (dec.includes('hash=')) return dec;
    }
  } catch { /* noop */ }
  return '';
}

export function initTelegram() {
  if (initialized) return;
  initialized = true;

  // 1) СИНХРОННО: главный источник — готов initData прямо сейчас
  const raw = extractInitData();
  if (raw) initDataRaw.value = raw;

  // 2) видимая подстройка под Telegram (цвета/раскрытие) — тоже сразу
  try {
    const wa = window.Telegram?.WebApp;
    if (wa) {
      try { wa.expand(); } catch { /* noop */ }
      try { wa.setHeaderColor('#0E0A12'); wa.setBackgroundColor('#0E0A12'); }
      catch { /* noop */ }
    }
  } catch { /* noop */ }

  // 3) SDK как позднее обогащение: если вдруг NgApp.initData пуст,
  //    SDK вытаскивает launch params (не влияет на рендер)
  try {
    import('@telegram-apps/sdk').then(async (sdk) => {
      try { if (sdk && typeof sdk.init === 'function') await sdk.init(); }
      catch { /* фолбэк уже сработал */ }
      try {
        const lp = typeof sdk.retrieveLaunchParams === 'function'
          ? sdk.retrieveLaunchParams() : null;
        if (lp && lp.initDataRaw && !initDataRaw.value) {
          initDataRaw.value = lp.initDataRaw;
        }
      } catch { /* ничего */ }
    }).catch(() => {});
  } catch { /* noop */ }
}

export function firstUserName() {
  try {
    const wa = window.Telegram?.WebApp;
    const u = wa?.initDataUnsafe?.user;
    return u?.first_name || u?.username || '';
  } catch { return ''; }
}

/** Премиум-отклик: Telegram HapticFeedback (безопасно игнорируется вне ТГ). */
export function haptic(kind = 'light') {
  try {
    const hf = window.Telegram?.WebApp?.HapticFeedback;
    if (!hf) return;
    if (kind === 'select') hf.selectionChanged();
    else if (kind === 'ok' || kind === 'error' || kind === 'warning') hf.notificationOccurred(kind);
    else hf.impactOccurred(kind);   // light | medium | rigid | soft
  } catch { /* noop */ }
}
