/* Общий UI-слой Vibe v3: иконки (единый штрих), карточки, рельсы,
   ряды треков, контекст-меню, шоты, скелетоны, визуальные утилиты. */
import React, { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useAnimationControls } from 'motion/react';
import { api, coverUrl } from './api.js';
import { haptic } from './tg.js';
import { toggleLike, useLikes } from './likes.js';
import * as P from './player.js';
import AnimatedCard from './motion/components/AnimatedCard.jsx';
import AnimatedIcon from './motion/components/AnimatedIcon.jsx';
import Sheet from './motion/components/Sheet.jsx';
import { toast } from './motion/utils/toast.js';

/* ---------------- иконки (stroke 2, единый оптический ритм) ---------------- */
const Icon = ({ d, children, ...props }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
       width="1em" height="1em" aria-hidden="true" {...props}>
    {d ? <path d={d} /> : null}
    {children}
  </svg>
);

export const IcoHome = p => <Icon {...p}><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h5v-6h4v6h5V9.5" /></Icon>;
export const IcoWave = p => <Icon {...p}><path d="M3 12h2.5M8 7v10M12 4.5v15M16 8.5v7M20 12h1.5" /></Icon>;
export const IcoSearch = p => <Icon {...p}><circle cx="11" cy="11" r="7" /><path d="m20.5 20.5-4.6-4.6" /></Icon>;
export const IcoLib = p => <Icon {...p}><path d="M4 4h6M4 9h6M14 4h6M14 9h6M4 14h16M4 19h9" /><path d="M17 14v6.5" /><circle cx="17" cy="20.5" r="0" /></Icon>;
export const IcoImport = p => <Icon {...p}><path d="M12 3v10" /><path d="m7.5 9 4.5 4.5L16.5 9" /><path d="M4 17.5h16" /></Icon>;
export const IcoPodcast = p => <Icon {...p}><rect x="9" y="2.8" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0" /><path d="M12 17.5V21" /></Icon>;
export const IcoQueue = p => <Icon {...p}><path d="M3 6h13M3 11h13M3 16h8" /><circle cx="18" cy="16.5" r="2.6" fill="currentColor" stroke="none" /><path d="M20.6 16.5V9.5l-4-1v6" /></Icon>;
export const IcoExpand = p => <Icon {...p}><path d="m6 14 6-6 6 6" strokeDasharray="0 0" opacity="0" /><path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M3 16v3a2 2 0 0 0 2 2h3M21 16v3a2 2 0 0 1-2 2h-3" /></Icon>;
export const IcoBell = p => <Icon {...p}><path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6" /><path d="M10.3 20a2 2 0 0 0 3.4 0" /></Icon>;
export const IcoHeart = ({ filled, ...p }) =>
  <Icon {...p}><path d="M12 21c-4.9-4.2-9-7.4-9-11.4C3 6.5 5.4 4 8.5 4c1.7 0 3.4.8 4.5 2.1C14.1 4.8 15.8 4 17.5 4 20.6 4 23 6.5 23 9.6c0 4-4.1 7.2-9 11.4z" fill={filled ? 'currentColor' : 'none'} /></Icon>;
export const IcoDl = p => <Icon {...p}><path d="M12 3.5v11" /><path d="m7.5 10 4.5 4.5L16.5 10" /><path d="M4.5 20h15" /></Icon>;
export const IcoDownload = IcoDl;              // совместимость со старыми импортами
export const IcoMore = p => <Icon {...p}><circle cx="12" cy="5.4" r="1.7" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1.7" fill="currentColor" stroke="none" /><circle cx="12" cy="18.6" r="1.7" fill="currentColor" stroke="none" /></Icon>;
export const IcoPlus = p => <Icon {...p}><path d="M12 5v14M5 12h14" /></Icon>;
export const IcoX = p => <Icon {...p}><path d="m6 6 12 12M18 6 6 18" /></Icon>;
export const IcoCheck = p => <Icon {...p}><path d="m4.5 12.5 5 5 10-11" /></Icon>;
export const IcoChevL = p => <Icon {...p}><path d="M15 5l-7 7 7 7" /></Icon>;
export const IcoChevR = p => <Icon {...p}><path d="m9 5 7 7-7 7" /></Icon>;
export const IcoPrev = p => <Icon {...p}><path d="M18.4 5.4v13.2L7.4 12z" fill="currentColor" stroke="none" /><rect x="4" y="5.4" width="2.4" height="13.2" rx="1.2" fill="currentColor" stroke="none" /></Icon>;
export const IcoNext = p => <Icon {...p}><path d="M5.6 5.4v13.2L16.6 12z" fill="currentColor" stroke="none" /><rect x="17.6" y="5.4" width="2.4" height="13.2" rx="1.2" fill="currentColor" stroke="none" /></Icon>;
export const IcoShuffle = p => <Icon {...p}><path d="M16 3.5h4.5V8" /><path d="M4 20 20.5 3.5" /><path d="M20.5 16v4.5H16" /><path d="m15 15 5.5 5.5" /><path d="M4 4l5.5 5.5" /></Icon>;
export const IcoRepeat = p => <Icon {...p}><path d="m17 2.5 3.5 3.5L17 9.5" /><path d="M3.5 12V9.5a3.5 3.5 0 0 1 3.5-3.5h13.5" /><path d="m7 21.5-3.5-3.5L7 14.5" /><path d="M20.5 12v2.5a3.5 3.5 0 0 1-3.5 3.5H3.5" /></Icon>;
export const IcoVol = p => <Icon {...p}><path d="M4 9.5h3.5L12 5.5v13L7.5 14.5H4z" fill="currentColor" stroke="none" /><path d="M15.5 9a4.3 4.3 0 0 1 0 6" /><path d="M18.3 6.6a8 8 0 0 1 0 10.8" /></Icon>;
export const IcoVolOff = p => <Icon {...p}><path d="M4 9.5h3.5L12 5.5v13L7.5 14.5H4z" fill="currentColor" stroke="none" /><path d="m16.5 9.5 5 5M21.5 9.5l-5 5" /></Icon>;
export const IcoClock = p => <Icon {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></Icon>;
export const IcoNote = p => <Icon {...p}><path d="M9 18.5V5.5l11-2.2v13" /><circle cx="6.4" cy="18.5" r="2.6" fill="currentColor" stroke="none" /><circle cx="17.4" cy="16.3" r="2.6" fill="currentColor" stroke="none" /></Icon>;
export const IcoMic = IcoPodcast;
export const IcoDislike = p => <Icon {...p}><circle cx="12" cy="14.5" r="5" /><path d="M8 8l-1.6-3.4M16 8l1.6-3.4M12 7.6V4" /><path d="M4.5 4.5 19.5 19.5" /></Icon>;
export const IcoGear = p => <Icon {...p}><circle cx="12" cy="12" r="3.2" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 9 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.88-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1z" /></Icon>;

/* ---------------- прiметивы визуала ---------------- */
export function coverOr(t) {
  const u = t && (t.cover || t.cover_url);
  const r = coverUrl(u ? { cover_url: u } : null);
  return r || t?.gradient || null;
}

/* Обложка проявляется после загрузки: без резкой замены пустого блока
   на яркое изображение. */
export function FadeImg({ src, alt = '', className = '', onError, ...rest }) {
  const ref = useRef(null);
  const [ok, setOk] = useState(false);

  useEffect(() => {
    setOk(false);
    const el = ref.current;
    if (el && el.complete && el.naturalWidth) setOk(true);
  }, [src]);

  if (!src) return null;
  return (
    <img
      ref={ref}
      src={src}
      alt={alt}
      loading="lazy"
      decoding="async"
      className={'fadeimg' + (ok ? ' on' : '') + (className ? ' ' + className : '')}
      onLoad={() => setOk(true)}
      onError={e => { e.currentTarget.style.visibility = 'hidden'; onError?.(e); }}
      {...rest}
    />
  );
}

/* Эквалайзер — только индикатор реального воспроизведения.
   При паузе не исчезает скачком, а замирает в спокойном состоянии. */
export function Eq({ playing = true, big = false }) {
  return (
    <span className={'eq' + (big ? ' eq-lg' : '') + (playing ? '' : ' paused')} aria-hidden="true">
      <i /><i /><i />
    </span>
  );
}

/* детерминированные градиенты для плейсхолдеров (без картинок) */
const GRADS = [
  'linear-gradient(140deg,#3a4256,#20242c)',
  'linear-gradient(140deg,#5a4d88,#26203a)',
  'linear-gradient(140deg,#2e5a52,#1c2c2a)',
  'linear-gradient(140deg,#6a4438,#31201a)',
  'linear-gradient(140deg,#40506e,#1e2635)',
  'linear-gradient(140deg,#655d33,#2a2a1c)',
  'linear-gradient(140deg,#573345,#2b1a22)',
  'linear-gradient(140deg,#345c63,#18262b)',
];
export function gradFor(seed) {
  if (!seed) return GRADS[0];
  let h = 0;
  for (let i = 0; i < String(seed).length; i++) h = (h * 31 + String(seed).charCodeAt(i)) >>> 0;
  return GRADS[h % GRADS.length];
}

/* Цвет фона/свечения: доминирующий цвет обложки (аналог ColorThief, 0 зависимостей). */
export function dominantColor(src) {
  return new Promise(resolve => {
    if (!src) return resolve(null);
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      try {
        const S = 22;
        const cv = document.createElement('canvas');
        cv.width = S; cv.height = S;
        const ctx = cv.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, S, S);
        const d = ctx.getImageData(0, 0, S, S).data;
        const buckets = new Map();
        for (let i = 0; i < d.length; i += 4) {
          if (d[i + 3] < 140) continue;
          const r = d[i], g = d[i + 1], b = d[i + 2];
          const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
          const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
          if (lum < 0.10 || lum > 0.94) continue;
          const sat = mx ? (mx - mn) / mx : 0;
          const key = ((r >> 5) << 6) | ((g >> 5) << 3) | (b >> 5);
          const bk = buckets.get(key) || { n: 0, r: 0, g: 0, b: 0, s: 0 };
          bk.n++; bk.r += r; bk.g += g; bk.b += b; bk.s += sat;
          buckets.set(key, bk);
        }
        let best = null, score = 0;
        for (const bk of buckets.values()) {
          const sc = bk.n * (0.4 + bk.s / bk.n);
          if (sc > score) { score = sc; best = bk; }
        }
        if (!best) return resolve(null);
        resolve([Math.round(best.r / best.n), Math.round(best.g / best.n),
                 Math.round(best.b / best.n)]);
      } catch { resolve(null); }
    };
    img.onerror = () => resolve(null);
    img.src = src;
  });
}

export function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  const w = (m10 === 1 && m100 !== 11) ? one
    : (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) ? few : many;
  return `${n} ${w}`;
}

export function fmtTime(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}

/* ---------------- обложечная плитка (built into PlayCard) ---------------- */

/* ---------------- карточка контента (альбом / плейлист / подкаст) ---------------- */
export function PlayCard({ title, sub, t, grad, glyph, badge, onOpen, onPlay, cellDelay, ratio, radius, instant }) {
  const src = coverOr(t);
  return (
    <AnimatedCard className="card" instant={instant}
                  delay={cellDelay != null ? Math.round(cellDelay / 40) : 0}>
      <div className="cardcov" style={radius ? { borderRadius: radius } : undefined} onClick={onOpen}>
        {src
          ? <FadeImg src={src} alt="" style={{ width: '100%', height: ratio ? '100%' : undefined }} />
          : <span className="cph" style={{ background: grad || gradFor(title || 'v') }}>
              {glyph || <IcoNote width="1em" height="1em" style={{ width: '28%', height: '28%' }} />}
            </span>}
        {badge && <span className="badge">{badge}</span>}
        <span className="cardclick" onClick={onOpen} />
        {onPlay && (
          <motion.button className="cplay" aria-label={`Слушать: ${title || ''}`}
                         whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.93 }}
                         transition={{ type: 'spring', stiffness: 260, damping: 38, mass: 1 }}
                         onPointerDown={e => e.stopPropagation()}
                         onClick={e => { e.stopPropagation(); haptic('light'); onPlay(e); }}>
            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8.5 5.2v13.6L19.2 12z" /></svg>
          </motion.button>
        )}
      </div>
      <span className="cardname" onClick={onOpen}>{title}</span>
      {sub != null && <span className="cardsub" onClick={onOpen}>{sub}</span>}
    </AnimatedCard>
  );
}

/* карточка исполнителя (круглый аватар) */
export function ArtistCard({ name, sub, cover, onOpen, delay, instant }) {
  return (
    <AnimatedCard className="artcard" instant={instant}
                  delay={delay != null ? Math.round(delay / 40) : 0}
                  onClick={() => { haptic('light'); onOpen?.(); }}>
      <span className="artava">
        {cover ? <FadeImg src={coverOr({ cover_url: cover })} alt="" />
               : <span className="artinit">{(name || '?').slice(0, 1).toUpperCase()}</span>}
      </span>
      <span className="artname">{name}</span>
      {sub && <span className="artsub">{sub}</span>}
    </AnimatedCard>
  );
}

/* горизонтальная рельса со стрелками (десктоп) */
export function Rail({ children }) {
  const scroller = React.useRef(null);
  const scroll = dir => {
    const el = scroller.current;
    if (!el) return;
    el.scrollBy({ left: dir * Math.max(320, el.clientWidth * 0.6), behavior: 'smooth' });
  };
  return (
    <div className="railwrap">
      <button className="rarr l" aria-label="Листать назад" onClick={() => { haptic('soft'); scroll(-1); }}><IcoChevL /></button>
      <div className="rail" ref={scroller}>{children}</div>
      <button className="rarr r" aria-label="Листать вперёд" onClick={() => { haptic('soft'); scroll(1); }}><IcoChevR /></button>
    </div>
  );
}

/* ---------------- ряд трека ---------------- */
export function TrackRow({ t, i, onPlay, num, time, heart = true, onMenu, children, layout = false }) {
  /* Лёгкая подписка вместо полного снапшота: строка перерисовывается
     только при смене трека/play-pause, а не на каждом timeupdate. */
  const [curId, curRun] = P.useCurKey().split(':');
  const isCur = curId !== '' && +curId === t.id;
  const playing = isCur && curRun === '1';
  const cov = coverOr(t);
  /* Вход без y-сдвига и только у первых строк: в больших списках (500+)
     волна transform-анимаций + layout-измерения «вешали» переход и дёргали
     скролл. Остальные строки рендерятся сразу (initial=false). */
  const wake = (i ?? 0) < 14;
  /* «уже в чате»: бэкенд отдаёт ready (file_id закеширован после отправки
     в чат); после успешной отправки из приложения ставим локально. */
  const [dl, setDl] = useState(null);          // null | { s: 'busy'|'done', txt }
  const inChat = dl?.s === 'done' || t.ready;
  const sendToChat = () => {
    if (dl?.s === 'busy') return;
    setDl({ s: 'busy', txt: '…' });
    downloadTrack(t, txt => {
      if (txt === 'в очереди') setDl({ s: 'busy', txt: '…' });
      else if (txt.startsWith('⬇')) setDl({ s: 'busy', txt: txt.slice(2) });
      else if (txt.startsWith('✓')) {
        setDl({ s: 'done', txt: '' });
        toast('Файл отправлен в чат', { kind: 'success' });
      } else {
        setDl(null);
        toast(txt === 'ошибка' ? 'Не удалось отправить в чат' : txt,
              { kind: 'error' });
      }
    }).catch(() => setDl(null));
  };
  return (
    <motion.div
      layout={layout}
      className={'trow' + (playing ? ' playing' : '')}
      initial={wake ? { opacity: 0 } : false}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, transition: { duration: .16, ease: [.4, 0, 1, 1] } }}
      transition={{ duration: .2, ease: [.16, 1, .3, 1], delay: wake ? (i ?? 0) * 0.018 : 0 }}
      onClick={() => { haptic('light'); onPlay?.(); }}
    >
      {num && (
        <span className="num">
          {playing ? <Eq /> : <>
            <span className="num-n">{num}</span>
            <span className="num-play" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8.6 5.4v13.2L19.4 12z" /></svg>
            </span>
          </>}
        </span>
      )}
      <span className="covwrap">
        {cov
          ? <FadeImg className="cov" src={cov} alt="" loading="lazy" />
          : <span className="cph" style={{ borderRadius: 8, flex: '0 0 auto',
                                           background: gradFor(t.title || t.album || 'x') }}
                  aria-hidden="true"><IcoNote width="1em" height="1em" /></span>}
        {inChat && (
          <span className="chatok" title="Уже скачан в чат"
                aria-label="Уже скачан в чат">
            <IcoDl width="10px" height="10px" />
          </span>
        )}
      </span>
      <div className="tmeta">
        <div className="tt">{t.title}</div>
        <div className="ta">{t.artist}{t.album ? ' · ' + t.album : ''}</div>
      </div>
      {time && <span className="ttime">{fmtTime(t.duration)}</span>}
      {!inChat && (
        <button className={'tdl' + (dl?.s === 'busy' ? ' busy' : '')}
                aria-label="Скачать в чат" title="Скачать в чат"
                onClick={e => { e.stopPropagation(); haptic('light'); sendToChat(); }}>
          {dl?.s === 'busy'
            ? <span className="tdl-p">{dl.txt}</span>
            : <IcoDl />}
        </button>
      )}
      {heart && <HeartBtn id={t.id} />}
      {children}
      {onMenu && (
        <button className="tmark" aria-label="Меню трека"
                onClick={e => { e.stopPropagation(); haptic('soft'); onMenu(e); }}>
          <IcoMore />
        </button>
      )}
    </motion.div>
  );
}

export function HeartBtn({ id, big }) {
  const liked = useLikes().has(id);
  const controls = useAnimationControls();
  return (
    <motion.button
      className={'heart' + (liked ? ' on' : '') + (big ? ' lg' : '')}
      aria-label="В любимое"
      whileTap={{ scale: 0.9 }}
      transition={{ type: 'spring', stiffness: 240, damping: 36, mass: 1 }}
      onClick={async e => {
        e.stopPropagation();
        haptic(liked ? 'soft' : 'light');
        const now = await toggleLike(id);
        if (now) {
          controls.start({ scale: [1, 1.18, 1] }, { duration: .32, ease: [.2, .8, .2, 1] });
        }
      }}
      animate={controls}
    >
      <svg viewBox="0 0 24 24">
        <path fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"
              d="M12 21c-4.6-4-8.7-7-8.7-10.8C3.3 7 5.6 4.7 8.4 4.7c1.6 0 3.1.8 4.1 2 1-1.2 2.4-2 4-2 2.8 0 5.1 2.3 5.1 5.5 0 3.8-4.1 6.8-8.7 10.8z" />
      </svg>
    </motion.button>
  );
}

/* морф play/pause: обе иконки живут одновременно, переход — crossfade */
export function PlayPause({ playing, loading, big }) {
  return (
    <span className={'ppm' + (big ? ' lg' : '') + (playing ? ' on' : '')} aria-hidden="true">
      {loading
        ? <motion.span className="spinner"
                       initial={{ opacity: 0, scale: .8 }}
                       animate={{ opacity: 1, scale: 1 }}
                       transition={{ duration: .16, ease: [.16, 1, .3, 1] }} />
        : <>
          <motion.svg className="pp-ico pp-play" viewBox="0 0 24 24" fill="currentColor"
                      animate={{ opacity: playing ? 0 : 1, scale: playing ? .62 : 1, rotate: playing ? 7 : 0 }}
                      transition={{ duration: .17, ease: [.16, 1, .3, 1] }}>
            <path d="M8.2 5.6v12.8L18.6 12z" />
          </motion.svg>
          <motion.svg className="pp-ico pp-pause" viewBox="0 0 24 24" fill="currentColor"
                      animate={{ opacity: playing ? 1 : 0, scale: playing ? 1 : .62, rotate: playing ? 0 : -7 }}
                      transition={{ duration: .17, ease: [.16, 1, .3, 1] }}>
            <rect x="6.6" y="5.4" width="3.6" height="13.2" rx="1.7" />
            <rect x="13.8" y="5.4" width="3.6" height="13.2" rx="1.7" />
          </motion.svg>
        </>}
    </span>
  );
}

/* ---------------- скачать в чат (поллинг job) ---------------- */
export async function downloadTrack(track, onStatus) {
  onStatus?.('в очереди');
  let fin = 'ошибка';
  try {
    const { job } = await api('/api/download?track_id=' + track.id, { method: 'POST' });
    let it = 0;
    while (it++ < 400) {
      const s = await api('/api/jobs/' + job);
      fin = s.status === 'done' ? '✓ в чате'
        : s.status === 'error' ? 'ошибка'
        : '⬇ ' + Math.round((s.progress || 0) * 100) + '%';
      onStatus?.(fin);
      if (s.status === 'done' || s.status === 'error') break;
      await new Promise(r => setTimeout(r, 1500));
    }
  } catch (e) {
    const msg = e.message || 'ошибка';
    fin = msg === 'telegram_required' ? 'Нужен вход в Telegram' : msg;
    onStatus?.(fin);
    return msg === 'telegram_required' ? 'telegram_required' : fin;
  }
  return fin;
}

/* ---------------- шит-меню трека ---------------- */
export function TrackMenu({
  t, open, onClose, ptId, playlistId, onOpenArtist, onChanged, extraRows,
}) {
  const [pls, setPls] = useState(null);
  const [dpid, setDpid] = useState('');           // статус скачивания
  const [adding, setAdding] = useState(0);        // id плейлиста, куда идёт добавление
  const track = t || null;
  const shown = open ?? !!track;

  useEffect(() => {
    if (!shown) return;
    api('/api/playlists').then(d => setPls(d.playlists || [])).catch(() => setPls([]));
  }, [shown]);

  const add = async (pl) => {
    if (!track) return;
    setAdding(pl.id);
    try {
      await api(`/api/playlists/${pl.id}/tracks`, { method: 'POST', body: JSON.stringify({ track_id: track.id }) });
      haptic('ok');
      toast(`Добавлено в «${pl.title}»`, { kind: 'success' });
      onChanged?.();
      onClose();
    } catch { setAdding(0); toast('Не удалось добавить трек', { kind: 'error' }); }
  };

  const remove = async () => {
    if (!ptId || !playlistId) return;
    try {
      await api(`/api/playlists/${playlistId}/tracks/${ptId}`, { method: 'DELETE' });
      haptic('ok');
      toast('Трек убран из плейлиста', { kind: 'success' });
      onChanged?.();
      onClose();
    } catch { toast('Не удалось убрать трек', { kind: 'error' }); }
  };

  const liked = useLikes().has(track?.id);

  return (
    <Sheet open={!!shown && !!track} onClose={onClose} labelledBy="track-menu-title">
      {track && (
        <>
          <div id="track-menu-title" className="sr-only">Действия с треком</div>
          <div className="shrow" style={{ pointerEvents: 'none' }}>
            <FadeImg style={{ width: 42, height: 42, borderRadius: 8, objectFit: 'cover' }}
                     src={coverOr(track)} alt="" />
            <div className="tmeta">
              <div className="tt">{track.title}</div>
              <div className="ta">{track.artist}</div>
            </div>
          </div>

          <button className="shrow" onClick={() => {
            haptic('light');
            toggleLike(track.id);
            toast(liked ? 'Убрано из «Любимого»' : 'Добавлено в «Любимое»', { kind: 'success' });
            onClose();
          }}>
            <IcoHeart filled={liked} /> {liked ? 'Убрать из «Любимого»' : 'В «Любимое»'}
          </button>

          <button className="shrow" onClick={async () => {
            haptic('medium');
            const fin = await downloadTrack(track, setDpid);
            if (fin.startsWith('✓')) {
              toast('Файл отправлен в чат', { kind: 'success' });
              onClose();
            } else if (fin === 'telegram_required') {
              onClose();
            } else if (fin === 'ошибка') {
              toast('Не удалось подготовить файл', { kind: 'error' });
            }
          }}>
            <IcoDl /> {dpid || (track.ready ? 'Отправить в чат снова' : 'Скачать в чат')}
          </button>

          {onOpenArtist && track.artist && (
            <button className="shrow" onClick={() => { haptic('select'); onOpenArtist(track.artist); }}>
              <IcoMic /> Исполнитель · {track.artist}
            </button>
          )}
          {extraRows}
          {ptId && playlistId && (
            <button className="shrow danger" onClick={remove}>
              <IcoX /> Убрать из плейлиста
            </button>
          )}

          <div className="shhead">Добавить в плейлист</div>
          {(pls || []).filter(p => p.id !== playlistId).slice(0, 12).map(pl => (
            <button key={pl.id} className="shrow" disabled={adding === pl.id} onClick={() => add(pl)}>
              <IcoPlus /> {adding === pl.id ? 'Добавляю…' : pl.title}
            </button>
          ))}
          {pls && !pls.length && <div className="pop-empty">Создай первый плейлист в Коллекции</div>}
        </>
      )}
    </Sheet>
  );
}

/* ---------------- группировка треков в альбомы (клиентский каталог) ---------------- */
export function groupAlbums(tracks) {
  const map = new Map();
  for (const t of tracks) {
    if (!t.album) continue;
    const key = ((t.artist || '') + '·' + t.album + '·' + (t.year || '')).toLowerCase();
    if (!map.has(key)) map.set(key, { key, title: t.album, artist: t.artist, year: t.year || null, tracks: [] });
    const a = map.get(key);
    a.tracks.push(t);
    if (!a.cover && t.cover_url) a.cover = t.cover_url;
    if (!a.year && t.year) a.year = t.year;
  }
  return [...map.values()];
}

/* ---------------- шапка секции ---------------- */
export function SectionHead({ title, action, onAction }) {
  return (
    <div className="sechead">
      <h2>{title}</h2>
      {action && <button className="more" onClick={onAction}>{action}<IcoChevR /></button>}
    </div>
  );
}

/* ---------------- скелетоны ---------------- */
export function Skeletons({ n = 7, grid = false }) {
  if (grid) return (
    <div className="albgrid">
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} style={{ animationDelay: i * 60 + 'ms' }}>
          <div className="skel skcov" />
          <div className="skel skline w70" />
        </div>
      ))}
    </div>
  );
  return (
    <div className="sk-rows">
      {Array.from({ length: n }).map((_, i) => (
        <div className="sk-row" key={i}>
          <div className="skel sk-cov" />
          <div className="sk-meta">
            <div className="skel skline w70" style={{ margin: 0 }} />
            <div className="skel skline w40" style={{ margin: 0 }} />
          </div>
        </div>
      ))}
    </div>
  );
}
