/* LOCK-ANIME §9.1: живая обложка Now Playing (экран блокировки) —
   аниме-портреты полным фоном. Пока приложение скрыто (локскрин / свитчер)
   и трек играет, artwork циклически сменяет 3 портрета («монтаж» как в
   клипе); на паузе — первый портрет затемняется с иконкой паузы.
   Источники — свободные фото с Wikimedia Commons, см. assets/CREDITS.md.
   Рендер: <img> → canvas 512×512 (cover-кроп) → PNG dataURL, кадры кешируются.
   iOS/WebKit игнорирует metadata.artwork — player.js дополнительно ставит
   тот же кадр как poster видео-элемента (хук onLockFrame). */
import lock1 from '../assets/lock/lock1.jpg';
import lock2 from '../assets/lock/lock2.jpg';
import lock3 from '../assets/lock/lock3.jpg';
import pro1 from '../assets/lock/pro_blackgirl.jpg';
import pro2 from '../assets/lock/pro_orangegirl.jpg';
import pro3 from '../assets/lock/pro_animegirl.jpg';

const SLEEP = 3;
const CYCLE = [0, 1, 2];
const PHOTOS = [lock1, lock2, lock3];
/* Vibe Pro: эксклюзивные кадры + золотой значок PRO в углу */
const PRO_CYCLE = [4, 5, 6];
const PRO_PHOTOS = { 4: pro1, 5: pro2, 6: pro3 };

/* золотой бейдж PRO в правом верхнем углу */
const PRO_BADGE = (ctx) => {
  ctx.save();
  const w = 96, h = 40, x = 512 - w - 18, y = 18;
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x, y, w, h, 12); else ctx.rect(x, y, w, h);
  ctx.fillStyle = 'rgba(12,14,28,.72)';
  ctx.fill();
  ctx.strokeStyle = '#FFD36E';
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.fillStyle = '#FFD36E';
  ctx.font = '800 22px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('★ PRO', x + w / 2, y + h / 2 + 1);
  ctx.restore();
};

/* пауза: затемнение + две «кнопки» паузы по центру */
const SLEEP_OVERLAY = (ctx) => {
  ctx.fillStyle = 'rgba(6,8,20,.55)';
  ctx.fillRect(0, 0, 512, 512);
  ctx.fillStyle = 'rgba(255,255,255,.92)';
  const w = 26, h = 84, gap = 24, x = 256 - gap / 2 - w, y = 256 - h / 2;
  for (const bx of [x, x + w + gap]) {
    ctx.beginPath();
    ctx.roundRect ? ctx.roundRect(bx, y, w, h, 10) : ctx.rect(bx, y, w, h);
    ctx.fill();
  }
};

let canvas = null;
const rasterCache = new Map();

/* фото-кадр → PNG dataURL (асинхронно, с кешем); i=3 — пауза */
async function rasterize(i) {
  if (rasterCache.has(i)) return rasterCache.get(i);
  const p = new Promise((resolve) => {
    try {
      const img = new Image();
      img.onload = () => {
        try {
          if (!canvas) canvas = document.createElement('canvas');
          canvas.width = canvas.height = 512;
          const ctx = canvas.getContext('2d');
          /* cover-кроп: фото целиком как фон */
          const s = Math.max(512 / img.width, 512 / img.height);
          const w = img.width * s, h = img.height * s;
          ctx.drawImage(img, (512 - w) / 2, (512 - h) / 2, w, h);
          if (i === SLEEP) SLEEP_OVERLAY(ctx);
          if (i >= 4) PRO_BADGE(ctx);
          resolve(canvas.toDataURL('image/png'));
        } catch { resolve(null); }
      };
      img.onerror = () => resolve(null);
      img.src = i === SLEEP ? PHOTOS[0] : (PRO_PHOTOS[i] || PHOTOS[i % PHOTOS.length]);
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

/* Состояние из плеера: играет → «монтаж» портретов, пауза → затемнённый кадр.
   Vibe Pro: эксклюзивные кадры с бейджем (PRO_CYCLE). */
let playing = false;
let cycleIdx = 0;
let isPro = false;

export function setLockPro(v) {
  isPro = !!v;
  if (currentFrame >= 0) currentFrame = -1;   // перерисовать кадр под тариф
  lockAnimeState(playing);
}

export function lockAnimeState(isPlaying) {
  playing = !!isPlaying;
  const cycle = isPro ? PRO_CYCLE : CYCLE;
  setLockFrame(playing ? cycle[cycleIdx % cycle.length] : SLEEP).catch(() => {});
}

function tick() {
  if (!playing || typeof document === 'undefined' || document.visibilityState !== 'hidden') return;
  const cycle = isPro ? PRO_CYCLE : CYCLE;
  cycleIdx = (cycleIdx + 1) % cycle.length;
  setLockFrame(cycle[cycleIdx]).catch(() => {});
}

let booted = false;
export function bootLockAnime() {
  if (booted || typeof document === 'undefined') return;
  booted = true;
  setInterval(tick, 4200);
  document.addEventListener('visibilitychange', () => {
    /* уход под локскрин: сразу продолжаем смену кадров */
    if (document.visibilityState === 'hidden') tick();
  });
}
