/* LOCK-ANIME §9.1: живая обложка Media Session — аниме-тянка в неоновых
   наушниках. Когда приложение скрыто (экран блокировки / свитчер) и трек
   играет, обложка циклически меняет кадры (слушает → вайбит → подмигивает)
   — на локскрине это выглядит как анимация. На паузе тянка «засыпает».
   Рендер: SVG → canvas 512×512 → PNG dataURL, каждый кадр кешируется.
   Всё локально: без сети, без внешних шрифтов (ноты — векторные). */

const NOTE = (x, y, color, s = 1, a = 0) => `
  <g fill="${color}" transform="translate(${x} ${y}) rotate(${a}) scale(${s})">
    <ellipse cx="0" cy="0" rx="9" ry="6.5" transform="rotate(-20)"/>
    <rect x="6" y="-30" width="4.5" height="31" rx="2"/>
    <path d="M10.5-30q13 4 13 17-4-8-13-6z"/>
  </g>`;

const BASE = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <defs>
    <radialGradient id="bg" cx="50%" cy="36%" r="80%">
      <stop offset="0" stop-color="#1C1244"/><stop offset="1" stop-color="#0A0C18"/>
    </radialGradient>
    <linearGradient id="hair" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#HAIR1"/><stop offset="1" stop-color="#B44DFF"/>
    </linearGradient>
    <linearGradient id="cup" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#FF3D81"/><stop offset="1" stop-color="#38E1FF"/>
    </linearGradient>
    <linearGradient id="hood" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2B3163"/><stop offset="1" stop-color="#161B36"/>
    </linearGradient>
  </defs>
  <rect width="512" height="512" fill="url(#bg)"/>
  <circle cx="256" cy="240" r="158" fill="rgba(255,61,129,.13)"/>
  <circle cx="256" cy="240" r="158" fill="none" stroke="rgba(255,61,129,.35)" stroke-width="3"/>
  <path d="M256 356c-64 0-104 34-112 92h224c-8-58-48-92-112-92z" fill="url(#hood)"/>
  <path d="M232 330h48v34c0 12-48 12-48 0z" fill="#FFDCC8"/>
  <ellipse cx="256" cy="252" rx="86" ry="84" fill="#FFE9DC"/>
  <path d="M148 262c-8-96 44-142 108-142s116 46 108 142c-2 26-10 44-10 44l-26-6c8-52-4-92-30-108-20 16-78 24-116 8-16 18-24 52-20 96l-12 6c-6-8-10-22-2-40z" fill="url(#hair)"/>
  <path d="M170 214c22-26 60-34 86-22-20 14-24 28-20 44-26-4-50-10-66-22z" fill="#FF8FC5" opacity=".55"/>
  <ellipse cx="196" cy="286" rx="17" ry="9" fill="#FF9EC4" opacity=".6"/>
  <ellipse cx="316" cy="286" rx="17" ry="9" fill="#FF9EC4" opacity=".6"/>
  <path d="M152 250c-6-104 46-158 104-158s110 54 104 158" fill="none" stroke="#3A4170" stroke-width="16" stroke-linecap="round"/>
  <rect x="118" y="218" width="52" height="76" rx="24" fill="url(#cup)"/>
  <rect x="342" y="218" width="52" height="76" rx="24" fill="url(#cup)"/>
  <rect x="128" y="230" width="32" height="52" rx="15" fill="rgba(255,255,255,.22)"/>
  <rect x="352" y="230" width="32" height="52" rx="15" fill="rgba(255,255,255,.22)"/>
  EYES
  DECOR
</svg>`;

const EYES = {
  listen: `
    <g fill="#2A2140">
      <ellipse cx="216" cy="266" rx="17" ry="22"/>
      <ellipse cx="296" cy="266" rx="17" ry="22"/>
    </g>
    <circle cx="222" cy="258" r="6" fill="#fff"/>
    <circle cx="302" cy="258" r="6" fill="#fff"/>
    <circle cx="211" cy="272" r="3" fill="#fff" opacity=".8"/>
    <circle cx="291" cy="272" r="3" fill="#fff" opacity=".8"/>
    <path d="M244 292q12 10 24 0" stroke="#C4567A" stroke-width="5" fill="none" stroke-linecap="round"/>`,
  vibe: `
    <path d="M202 264q14 12 28 0" stroke="#2A2140" stroke-width="7" fill="none" stroke-linecap="round"/>
    <path d="M282 264q14 12 28 0" stroke="#2A2140" stroke-width="7" fill="none" stroke-linecap="round"/>
    <ellipse cx="256" cy="296" rx="9" ry="11" fill="#C4567A"/>`,
  wink: `
    <ellipse cx="216" cy="266" rx="17" ry="22" fill="#2A2140"/>
    <circle cx="222" cy="258" r="6" fill="#fff"/>
    <circle cx="211" cy="272" r="3" fill="#fff" opacity=".8"/>
    <path d="M282 266q14 10 28 0" stroke="#2A2140" stroke-width="7" fill="none" stroke-linecap="round"/>
    <path d="M240 294q16 12 30-2" stroke="#C4567A" stroke-width="5" fill="none" stroke-linecap="round"/>`,
  sleep: `
    <path d="M204 268q12 8 24 0" stroke="#2A2140" stroke-width="6" fill="none" stroke-linecap="round"/>
    <path d="M284 268q12 8 24 0" stroke="#2A2140" stroke-width="6" fill="none" stroke-linecap="round"/>
    <path d="M250 296q6 5 12 0" stroke="#C4567A" stroke-width="5" fill="none" stroke-linecap="round"/>`,
};

const DECOR = {
  listen: NOTE(382, 128, '#FFB7D5', 1.2, 12) + NOTE(110, 100, '#9FE8FF', 1, -14) + NOTE(416, 330, '#B44DFF', .9, 18),
  vibe: NOTE(400, 150, '#FFB7D5', 1.2, 24) + NOTE(96, 120, '#9FE8FF', 1, -20) + NOTE(140, 380, '#B44DFF', .9, -10) + `
    <g transform="translate(256 62) scale(1.35)"><path d="M0-14 4-4h10l-8 6 3 10-9-6-9 6 3-10-8-6h10z" fill="#FFD36E"/></g>`,
  wink: `
    <g fill="#FFD36E">
      <path d="M382 96l7 18 18 7-18 7-7 18-7-18-18-7 18-7z"/>
      <path d="M104 140l5 13 13 5-13 5-5 13-5-13-13-5 13-5z" opacity=".8"/>
    </g>` + NOTE(372, 320, '#9FE8FF', 1, 16),
  sleep: `
    <g fill="none" stroke="#9FE8FF" stroke-width="6" stroke-linecap="round" opacity=".9">
      <path d="M368 96h34a10 10 0 0 1 0 20h-10a10 10 0 0 0 0 20h34"/>
      <path d="M300 70h22a7 7 0 0 1 0 14h-7a7 7 0 0 0 0 14h22" opacity=".7"/>
    </g>
    <circle cx="128" cy="104" r="26" fill="#FFF3C9" opacity=".9"/>
    <circle cx="120" cy="98" r="7" fill="#E8D9A0" opacity=".8"/>`,
};

const SLEEP = 3;
const CYCLE = [0, 1, 2];
const FRAMES = [
  { svg: BASE.replace('EYES', EYES.listen).replace('DECOR', DECOR.listen).replace('#HAIR1', '#FF6FB3') },
  { svg: BASE.replace('EYES', EYES.vibe).replace('DECOR', DECOR.vibe).replace('#HAIR1', '#FF6FB3') },
  { svg: BASE.replace('EYES', EYES.wink).replace('DECOR', DECOR.wink).replace('#HAIR1', '#FF6FB3') },
  { svg: BASE.replace('EYES', EYES.sleep).replace('DECOR', DECOR.sleep).replace('#HAIR1', '#8F7BC8') },
];

let canvas = null;
const rasterCache = new Map();

/* SVG-кадр → PNG dataURL (асинхронно, с кешем) */
async function rasterize(i) {
  if (rasterCache.has(i)) return rasterCache.get(i);
  const p = new Promise((resolve) => {
    try {
      if (!canvas) canvas = document.createElement('canvas');
      canvas.width = canvas.height = 512;
      const ctx = canvas.getContext('2d');
      const img = new Image();
      img.onload = () => {
        try {
          ctx.clearRect(0, 0, 512, 512);
          ctx.drawImage(img, 0, 0, 512, 512);
          resolve(canvas.toDataURL('image/png'));
        } catch { resolve(null); }
      };
      img.onerror = () => resolve(null);
      img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(FRAMES[i].svg);
    } catch { resolve(null); }
  });
  rasterCache.set(i, p);
  return p;
}

let currentFrame = -1;

/* подписка на смену кадра (player.js ставит poster видео-элемента для iOS) */
const frameListeners = new Set();
export function onLockFrame(fn) {
  if (typeof fn !== 'function') return () => frameListeners.delete(fn);
  frameListeners.add(fn);
  /* если кадр уже отрисован — сразу отдаём */
  if (currentFrame >= 0) { rasterize(currentFrame).then(u => { if (u && currentFrame >= 0) { try { fn(u); } catch { /* ок */ } } }); }
  return () => frameListeners.delete(fn);
}

/* Поставить кадр как artwork Media Session (метаданные сохраняются) */
export async function setLockFrame(i) {
  if (currentFrame === i) return;
  currentFrame = i;
  const url = await rasterize(i);
  if (currentFrame !== i) return;         // пока декодировали — кадр сменился
  const { setArtwork } = await import('./mediasession.js');
  setArtwork(url ? [{ src: url, sizes: '512x512', type: 'image/png' }] : []);
  frameListeners.forEach(f => { try { f(url); } catch { /* ок */ } });
}

/* Состояние из плеера: играет → кадры цикла, пауза → «спит» */
let playing = false;
let cycleIdx = 0;

export function lockAnimeState(isPlaying) {
  playing = !!isPlaying;
  setLockFrame(playing ? CYCLE[cycleIdx] : SLEEP).catch(() => {});
}

function tick() {
  if (!playing || typeof document === 'undefined' || document.visibilityState !== 'hidden') return;
  cycleIdx = (cycleIdx + 1) % CYCLE.length;
  setLockFrame(CYCLE[cycleIdx]).catch(() => {});
}

let booted = false;
export function bootLockAnime() {
  if (booted || typeof document === 'undefined') return;
  booted = true;
  setInterval(tick, 3600);
  document.addEventListener('visibilitychange', () => {
    /* уход под локскрин: сразу продолжаем анимацию с нового кадра */
    if (document.visibilityState === 'hidden') tick();
  });
}
