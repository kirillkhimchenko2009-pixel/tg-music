/* Vibe Music v3 — премиальная оболочка: сайдбар (десктоп), топбар,
   нижний плеер-бар, полноэкранный нау-плей, таб-бар (мобайл).
   Маршруты живут в стек-роутере nav.js (назад/вперёд/buttons).
   Вся моторика — из единой системы src/motion. */
import React, { useEffect, useRef, useState } from 'react';
import { AnimatePresence, MotionConfig, animate, motion, useMotionValue } from 'motion/react';
import * as P from './player.js';
import { api, BOT_URL, ensureGuestToken, exchangeCode, hasAuth, isGuest, setUnauthorizedHandler } from './api.js';
import { loadLikes, useLikes } from './likes.js';
import { setAuthToken, initDataRaw } from './store.js';
import { firstUserName, haptic } from './tg.js';
import { navInit, navPush, navBack, navFwd, useNav, useNavMeta } from './nav.js';
import {
  coverOr, FadeImg, Eq, HeartBtn, PlayPause, fmtTime, gradFor,
  IcoHome, IcoWave, IcoSearch, IcoLib, IcoImport, IcoPodcast, IcoBook,
  IcoChevL, IcoChevR, IcoBell, IcoX, IcoCheck,
  IcoPrev, IcoNext, IcoShuffle, IcoRepeat, IcoVol, IcoVolOff,
  IcoExpand, IcoDl, IcoHeart, IcoClock, IcoNote, IcoStar,
} from './ui.jsx';
import {
  AnimatedDropdown, AnimatedIcon, Seek, Sheet, ToastHost,
  layoutSpring, motionConfig, pageVariants, toast,
  usePlayerMotion, useSharedTransition,
} from './motion/index.js';

import Home from './screens/Home.jsx';
import Collection, { PlaylistScreen } from './screens/Collection.jsx';
import SearchScreen from './screens/Search.jsx';
import ImportScreen from './screens/Import.jsx';
import Podcasts from './screens/Podcasts.jsx';
import Audiobooks from './screens/Audiobooks.jsx';
import Artist from './screens/Artist.jsx';
import Album from './screens/Album.jsx';
import Wave from './screens/Wave.jsx';
import Storage from './screens/Storage.jsx';
import Pro from './screens/Pro.jsx';
import { LyricsView } from './LyricsView.jsx';
import { extractPalette } from './local/palette.js';

/* компатибл-экспорт: часть экранов импортирует эти символы из App.jsx */
export { coverOr, HeartBtn, PlayPause };

const sleep = ms => new Promise(r => setTimeout(r, ms));

const NAV_MAIN = [
  { tab: 'home', icon: IcoHome, label: 'Главная' },
  { tab: 'wave', icon: IcoWave, label: 'Моя волна' },
  { tab: 'search', icon: IcoSearch, label: 'Поиск' },
];
const NAV_COLL = [
  { tab: 'collection', sub: null, icon: IcoLib, label: 'Моя коллекция' },
  { tab: 'collection', sub: 'playlists', icon: IcoNote, label: 'Плейлисты' },
  { tab: 'collection', sub: 'likes', icon: IcoHeart, label: 'Любимые треки' },
  { tab: 'collection', sub: 'albums', icon: IcoNote, label: 'Альбомы' },
  { tab: 'collection', sub: 'artists', icon: IcoNote, label: 'Исполнители' },
  { tab: 'collection', sub: 'history', icon: IcoClock, label: 'История' },
];
const NAV_MISC = [
  { tab: 'pro', icon: IcoStar, label: 'Vibe Pro' },
  { tab: 'import', icon: IcoImport, label: 'Импорт' },
  { tab: 'podcasts', icon: IcoPodcast, label: 'Подкасты' },
  { tab: 'audiobooks', icon: IcoBook, label: 'Аудиокниги' },
  { tab: 'storage', icon: IcoDl, label: 'Хранилище' },
];

const TABS_M = [
  { id: 'home', icon: IcoHome, label: 'Главная' },
  { id: 'wave', icon: IcoWave, label: 'Волна' },
  { id: 'search', icon: IcoSearch, label: 'Поиск' },
  { id: 'collection', icon: IcoLib, label: 'Коллекция' },
  { id: 'podcasts', icon: IcoPodcast, label: 'Подкасты' },
  { id: 'audiobooks', icon: IcoBook, label: 'Книги' },
  { id: 'import', icon: IcoImport, label: 'Импорт' },
];

export default function App() {
  const view = useNav();
  const [ok, setOk] = useState('boot');
  const [name, setName] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [login, setLogin] = useState(false);
  const [bootRetry, setBootRetry] = useState(0);
  const [bootError, setBootError] = useState('');

  useEffect(() => {
    const open = () => setLogin(true);
    window.addEventListener('vibe:login', open);
    return () => window.removeEventListener('vibe:login', open);
  }, []);

  useEffect(() => {
    if (location.hash) history.replaceState(null, '', location.pathname + location.search);
    let dead = false;
    let lastError = '';
    const boot = async () => {
      setOk('boot');
      for (let i = 0; i < 12 && !hasAuth() && !dead; i++) await sleep(100);
      for (let t = 0; t < 3 && !hasAuth() && !dead; t++) {
        if (t) await sleep(500);
        try { await ensureGuestToken(); }
        catch (e) {
          lastError = e?.message || 'нет связи с сервером';
          if (e?.name === 'AbortError') break;
        }
      }
      if (dead) return;
      if (hasAuth()) {
        setOk(true);
        setName(firstUserName() || localStorage.getItem('display_name') || 'Гость');
      } else {
        setBootError(lastError || 'Сервер не ответил. Проверь подключение и попробуй ещё раз.');
        setOk('error');
      }
      loadLikes();
    };
    navInit(startView());
    setUnauthorizedHandler(() => {
      setAuthToken('');
      setBootRetry(v => v + 1);
    });
    boot();
    return () => { dead = true; };
  }, [bootRetry]);

  if (ok !== true) return <BootSplash
    error={ok === 'error' ? bootError : ''}
    onRetry={() => { setBootError(''); setBootRetry(v => v + 1); }}
  />;

  return (
    <MotionConfig {...motionConfig}>
      <div className="app">
        <Sidebar view={view} />
        <div className="content">
          <TopBar name={name} />
          {/* Без exit-анимации: если она замирает (фоновая вкладка,
              приостановленный rAF), AnimatePresence mode="wait" вообще
              не монтирует новый экран — навигация «застревает» на старой
              странице. Вход нового экрана остаётся, уход — мгновенный. */}
          <motion.main
            key={viewKey(view)}
            className="page"
            variants={pageVariants}
            initial="initial"
            animate="enter"
          >
            <Screen view={view} />
          </motion.main>
        </div>
        <PlayerBar onExpand={() => setExpanded(true)} />
        <AnimatePresence>
          {expanded && <NowPlaying onClose={() => setExpanded(false)} />}
        </AnimatePresence>
        <TabBar tab={view} />
        <ToastHost />
        <LoginSheet open={login} onClose={() => setLogin(false)} />
      </div>
    </MotionConfig>
  );
}

function startView() {
  return /wave/i.test(location.hash || '') ? { tab: 'wave' } : { tab: 'home', sub: null };
}
function viewKey(v) {
  return (v.tab || 'home') + (v.sub ? ':' + v.sub : '') + (v.name ? ':' + v.name : '')
    + (v.album ? ':' + v.album.key : '');
}

function Screen({ view }) {
  switch (view.tab) {
    case 'wave': return <Wave />;
    case 'search': return <SearchScreen initial={view.q || ''} />;
    case 'collection': return <Collection sub={view.sub || null} />;
    case 'import': return <ImportScreen />;
    case 'podcasts': return <Podcasts />;
    case 'audiobooks': return <Audiobooks />;
    case 'storage': return <Storage />;
    case 'pro': return <Pro />;
    case 'artist': return <Artist name={view.name} cover={view.cover} />;
    case 'album': return <Album album={view.album} />;
    case 'playlist': return <PlaylistScreen plId={view.id} pl={view} />;
    default: return <Home />;
  }
}

/* ---------------- сайдбар ---------------- */
function isActive(view, item) {
  return view.tab === item.tab && (item.tab !== 'collection' || (view.sub || null) === (item.sub || null));
}

function SideItem({ view, item }) {
  const on = isActive(view, item);
  const Ico = item.icon;
  return (
    <motion.button
      className={'side-item' + (on ? ' on' : '')}
      whileTap={{ scale: 0.99 }}
      transition={{ type: 'spring', stiffness: 260, damping: 38, mass: 1 }}
      onClick={() => { haptic('soft'); navPush({ tab: item.tab, sub: item.sub || null }); }}
    >
      {on && <motion.span layoutId="side-active" className="side-plate" transition={layoutSpring} />}
      <span className="si"><Ico width="1em" height="1em" /></span>
      <span className="side-lbl">{item.label}</span>
      {item.soon && <span className="badge-dot" />}
    </motion.button>
  );
}

function Sidebar({ view }) {
  return (
    <aside className="side">
      <button className="side-link" onClick={() => { haptic('soft'); navPush({ tab: 'home', sub: null }); }}>
        <span className="ellipse"><svg viewBox="0 0 24 24" fill="currentColor">
          <path d="M9.4 18.2V6.4l9.8-2v11.4" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx="7" cy="18.2" r="2.4" /><circle cx="17.4" cy="15.8" r="2.4" />
        </svg></span>
        <span className="wordmark">Vibe<em>.</em></span>
      </button>

      <div className="side-group">Навигация</div>
      {NAV_MAIN.map(it => <SideItem key={it.tab} view={view} item={it} />)}

      <div className="side-group">Коллекция</div>
      {NAV_COLL.map(it => <SideItem key={it.tab + (it.sub || '')} view={view} item={it} />)}

      <div className="side-group">Ещё</div>
      {NAV_MISC.map(it => <SideItem key={it.tab} view={view} item={it} />)}

      <div className="side-foot">
        Vibe — личный стриминг в Telegram.<br />Волна, коллекция и скачивание в чат.
      </div>
    </aside>
  );
}

/* ---------------- topbar ---------------- */
function TopBar({ name }) {
  const meta = useNavMeta();
  const [scrolled, setScrolled] = useState(false);
  const [pop, setPop] = useState(null);   // 'bell' | 'user'
  useEffect(() => {
    const onScroll = () => setScrolled((document.scrollingElement?.scrollTop || 0) > 24);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <header className={'topbar' + (scrolled ? ' scrolled' : '')} onClick={() => pop && setPop(null)}>
      <button className="tb-back" aria-label="Назад" disabled={!meta.canBack}
              onClick={e => { e.stopPropagation(); haptic('soft'); navBack(); }}><IcoChevL /></button>
      <button className="tb-fwd" aria-label="Вперёд" disabled={!meta.canFwd}
              onClick={e => { e.stopPropagation(); haptic('soft'); navFwd(); }}><IcoChevR /></button>

      <button className="tb-search" onClick={e => { e.stopPropagation(); haptic('soft'); navPush({ tab: 'search', sub: null }); }}>
        <IcoSearch />
        <span className="ts-hint">Треки, исполнители, альбомы…</span>
      </button>

      <div style={{ flex: 1 }} />

      <div className="popwrap">
        <button className="tb-bell" aria-label="Уведомления"
                onClick={e => { e.stopPropagation(); setPop(p => p === 'bell' ? null : 'bell'); }}>
          <IcoBell />
        </button>
        <AnimatedDropdown show={pop === 'bell'} onClick={e => e.stopPropagation()}>
          <div className="pop-head"><div className="pop-title">Уведомления</div></div>
          <div className="pop-empty">Пока тихо: релизы и новости появятся здесь позже.</div>
        </AnimatedDropdown>
      </div>

      <div className="popwrap">
        <button className="tb-user" onClick={e => { e.stopPropagation(); setPop(p => p === 'user' ? null : 'user'); }}>
          <span className="tb-ava">{(name || 'Г').replace(/^@/, '').slice(0, 1).toUpperCase() || 'Г'}</span>
          <span className="tb-name">{name}</span>
        </button>
        <AnimatedDropdown show={pop === 'user'} onClick={e => e.stopPropagation()}>
          <div className="pop-head">
            <div className="pop-title">{name || 'Гость'}</div>
            <div className="pop-sub">{initDataRaw.value ? 'Аккаунт Telegram'
              : (isGuest() ? 'Гостевая сессия этого устройства' : 'Аккаунт Telegram · вход по коду')}</div>
          </div>
          {!initDataRaw.value && isGuest() && (
            <button className="pop-item"
                    onClick={() => { setPop(null); window.dispatchEvent(new Event('vibe:login')); }}>
              <span style={{ width: '1em', textAlign: 'center' }}>🔑</span> Войти по коду из бота
            </button>
          )}
          <button className="pop-item" onClick={() => { navPush({ tab: 'collection', sub: 'likes' }); setPop(null); }}>
            <IcoHeart width="1em" height="1em" /> Любимое
          </button>
          <button className="pop-item" onClick={() => { navPush({ tab: 'pro', sub: null }); setPop(null); }}>
            <IcoStar width="1em" height="1em" /> Подписка · Vibe Pro
          </button>
          <button className="pop-item" onClick={() => { navPush({ tab: 'collection', sub: 'history' }); setPop(null); }}>
            <IcoClock width="1em" height="1em" /> История
          </button>
          <button className="pop-item" onClick={() => { navPush({ tab: 'storage', sub: null }); setPop(null); }}>
            <IcoDl width="1em" height="1em" /> Хранилище · офлайн
          </button>
        </AnimatedDropdown>
      </div>
    </header>
  );
}

/* ---------------- boot ---------------- */
function BootSplash({ error, onRetry }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: .22, ease: [.16, 1, .3, 1] }}
      style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: 'var(--bg)' }}
    >
      <div className="cardbox" style={{ width: 'min(88vw, 340px)' }}>
        <div className="login">
          <div className="boot-eq"><i /><i /><i /></div>
          {error ? <>
            <h2>Не удалось подключиться</h2>
            <p className="muted" role="alert">{error}</p>
            <button className="bigbtn primary" onClick={onRetry}>Повторить</button>
          </> : <>
            <h2>Vibe запускается…</h2>
            <p className="muted">Подключаюсь к музыкальному серверу</p>
          </>}
        </div>
      </div>
    </motion.div>
  );
}

/* ---------------- таб-бар (мобайл) ---------------- */
function TabBar({ tab }) {
  const on = tab.tab || 'home';
  return (
    <nav className="tabbar">
      {TABS_M.map(t => {
        const Ico = t.icon;
        const active = on === t.id;
        return (
          <motion.button
            key={t.id}
            className={'tab' + (active ? ' on' : '')}
            whileTap={{ scale: 0.955 }}
            transition={{ type: 'spring', stiffness: 260, damping: 38, mass: 1 }}
            onClick={() => { haptic('soft'); navPush({ tab: t.id, sub: null }); }}
          >
            {active && <motion.span layoutId="tab-active" className="tab-plate" transition={layoutSpring} />}
            <span className="tab-ico"><Ico width="1em" height="1em" /></span>
            <span className="tab-lbl">{t.label}</span>
          </motion.button>
        );
      })}
    </nav>
  );
}

/* ---------------- плеер-бар ---------------- */
function PlayerBar({ onExpand }) {
  const st = P.usePlayerSnap();
  const { sharedCover } = useSharedTransition();
  const cur = st.queue[st.index];
  if (!cur) return null;
  return (
    <motion.div
      className="pbar"
      initial={{ opacity: 0, y: 26 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 190, damping: 34, mass: 1 }}
    >
      <div className="pl-left">
        <motion.div
          className="pcovwrap"
          layoutId="np-cover"
          transition={sharedCover}
          onClick={onExpand}
          role="button"
          tabIndex={0}
          aria-label="Открыть плеер"
          onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') onExpand(); }}
        >
          <FadeImg src={coverOr(cur)} alt="" />
          <Eq playing={st.playing} />
        </motion.div>

        <div className="pl-meta" onClick={onExpand}>
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.div
              key={cur.id}
              className="plmeta-in"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: .26, ease: [.16, 1, .3, 1] }}
            >
              <div className="pl-title">{cur.title}</div>
              <div className="pl-artist">{cur.artist}</div>
            </motion.div>
          </AnimatePresence>
        </div>

        <div className="pl-heart" onClick={e => e.stopPropagation()}><HeartBtn id={cur.id} /></div>
      </div>

      {/* десктоп: центр — прогресс + контролы */}
      <div className="pl-omni" onClick={e => e.stopPropagation()}>
        <div className="pl-lines">
          <Seek showTime ariaLabel="Позиция трека" />
        </div>
        <div className="pl-center">
          <button className={'pl-ctl' + (st.shuffle ? ' on' : '')} aria-label="Перемешать"
                  onClick={() => { haptic('light'); P.setShuffle(!st.shuffle); }}><IcoShuffle /></button>
          <button className="pl-ctl" aria-label="Предыдущий" onClick={() => { haptic('light'); P.prev(); }}><IcoPrev /></button>
          <motion.button className="pl-play" aria-label="Играть/пауза"
                         whileTap={{ scale: 0.94 }}
                         transition={{ type: 'spring', stiffness: 260, damping: 38, mass: 1 }}
                         onClick={() => { haptic('medium'); P.toggle(); }}>
            <PlayPause playing={st.playing} loading={st.loading} />
          </motion.button>
          <button className="pl-ctl" aria-label="Следующий" onClick={() => { haptic('light'); P.next(); }}><IcoNext /></button>
          <button className={'pl-ctl' + (st.loop ? ' on' : '')} aria-label="Повтор"
                  onClick={() => { haptic('light'); P.setLoop(!st.loop); }}><IcoRepeat /></button>
        </div>
      </div>

      {/* мобильный: компактные контролы */}
      <div className="pl-mctl" onClick={e => e.stopPropagation()}>
        <button className="pl-ctl" aria-label="Предыдущий" onClick={() => { haptic('light'); P.prev(); }}><IcoPrev /></button>
        <motion.button className="pl-play" aria-label="Играть/пауза"
                       whileTap={{ scale: 0.94 }}
                       transition={{ type: 'spring', stiffness: 260, damping: 38, mass: 1 }}
                       onClick={() => { haptic('medium'); P.toggle(); }}>
          <PlayPause playing={st.playing} loading={st.loading} />
        </motion.button>
        <button className="pl-ctl" aria-label="Следующий" onClick={() => { haptic('light'); P.next(); }}><IcoNext /></button>
      </div>

      {/* десктоп-право: громкость + раскрыть */}
      <div className="pl-right" onClick={e => e.stopPropagation()}>
        <div className="vol">
          <button className="pl-ctl" aria-label="Звук вкл/выкл"
                  onClick={() => P.toggleMute()}>
            {P.getState().volume > 0 ? <IcoVol /> : <IcoVolOff />}
          </button>
          <input className="seek" type="range" min="0" max="100"
                 value={Math.round(P.getState().volume * 100)}
                 style={{ '--fill': Math.round(P.getState().volume * 100) + '%' }}
                 aria-label="Громкость"
                 onChange={e => P.setVolume(+e.target.value / 100)} />
        </div>
        <button className="pl-ctl" aria-label="Раскрыть плеер" onClick={onExpand}><IcoExpand /></button>
      </div>

      {/* нитка прогресса по верхней кромке бара (мобайл) */}
      <div className="pbar-line" aria-hidden="true">
        <div className="pbar-line-fill"
             style={{ transform: `scaleX(${st.duration ? Math.min(1, st.currentTime / st.duration) : 0})` }} />
      </div>
    </motion.div>
  );
}

/* ---------------- нау-плей (полноэкранный) ---------------- */
function NowPlaying({ onClose }) {
  const st = P.usePlayerSnap();
  const cur = st.queue[st.index];
  const { sharedCover } = useSharedTransition();
  const { surfaceVariants, trackVariants, queueVariants, player } = usePlayerMotion();
  const [dl, setDl] = useState('');
  const [glow, setGlow] = useState(null);
  const [glow2, setGlow2] = useState(null);
  const y = useMotionValue(0);
  const scrollRef = useRef(null);
  const touch = useRef({ active: false, locked: false, startX: 0, startY: 0 });

  useEffect(() => {
    const esc = e => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [onClose]);

  /* §13: палитра по обложке — dominant → верхнее свечение (кроссфейд в
     .np-glow), accent → аура за обложкой; tainted canvas → базовая тема */
  useEffect(() => {
    let dead = false;
    extractPalette(coverOr(cur)).then(p => {
      if (dead) return;
      setGlow(p ? p.glow : null);
      setGlow2(p ? p.glow2 : null);
    });
    return () => { dead = true; };
  }, [cur?.id]);

  /* Свайп вниз закрывает плеер только от верхней кромки скролла;
     горизонтальный жест отпускает событие — вертикальный скролл не ломается. */
  const onTouchStart = e => {
    const el = scrollRef.current;
    if (!el || el.scrollTop > 0) return;
    /* скролл внутри блока лирики не должен тащить весь плеер вниз */
    const t0 = e.target;
    if (t0 && t0.closest && t0.closest('.lyr-box, .lyr-edit textarea, input[type="range"]')) {
      touch.current = { active: false, locked: false, startX: 0, startY: 0 };
      return;
    }
    const t = e.touches[0];
    touch.current = { active: true, locked: false, startX: t.clientX, startY: t.clientY };
  };
  const onTouchMove = e => {
    const d = touch.current;
    if (!d.active) return;
    const t = e.touches[0];
    const dy = t.clientY - d.startY;
    const dx = t.clientX - d.startX;
    if (!d.locked) {
      if (Math.abs(dy) < 8 && Math.abs(dx) < 8) return;
      if (Math.abs(dx) > Math.abs(dy) * 1.2) { d.active = false; return; }
      d.locked = true;
    }
    if (dy > 0) y.set(Math.min(dy * 0.85, 240));
  };
  const onTouchEnd = () => {
    const d = touch.current;
    if (!d.active) return;
    d.active = false;
    if (!d.locked) return;
    if (y.get() > 110) { haptic('soft'); onClose(); }
    else animate(y, 0, { type: 'spring', stiffness: 190, damping: 34, mass: 1 });
  };

  if (!cur) return null;

  const download = async () => {
    if (!cur || dl) return;
    haptic('medium');
    const fin = await downloadJob(cur, setDl);
    if (fin && fin.startsWith('✓')) toast('Файл отправлен в чат', { kind: 'success' });
    else if (fin === 'telegram_required') {
      toast('Войди по коду из бота — и файлы будут приходить в чат', { kind: 'info' });
    }
    else if (fin === 'Ошибка сети') toast('Не удалось скачать трек', { kind: 'error' });
    else if (fin && fin !== 'ошибка') toast('Не удалось: ' + fin, { kind: 'error' });
    setTimeout(() => setDl(''), 4000);
  };

  const dlBusy = dl && !dl.startsWith('✓');
  const dlDone = !!dl && dl.startsWith('✓');

  return (
    <motion.div
      ref={scrollRef}
      className="np"
      style={{ y,
               '--g': glow ? 'none' : undefined,
               '--g2': glow2 ? `radial-gradient(70% 70% at 50% 52%, ${glow2}, transparent 72%)` : undefined }}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: .3, ease: [.16, 1, .3, 1] }}
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
      onTouchCancel={onTouchEnd}
      role="dialog"
      aria-modal="true"
      aria-label="Сейчас играет"
    >
      <motion.div
        className="np-bg"
        variants={surfaceVariants}
        initial="hidden"
        animate="visible"
        exit="exit"
      />
      {/* §13: цветное свечение перекрёстно затухает при смене трека */}
      <AnimatePresence>
        {glow && (
          <motion.div key={'glow' + cur.id} className="np-glow"
                      initial={{ opacity: 0 }} animate={{ opacity: .5 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: .9, ease: [.16, 1, .3, 1] }}
                      style={{ background: glow }} />
        )}
      </AnimatePresence>
      <div className="np-in">
        <div className="np-head">
          <span className="np-kicker">
            <span className="np-now"><Eq playing={st.playing} /></span>
            Сейчас играет
          </span>
          <motion.button className="np-x" whileTap={{ scale: 0.94 }} onClick={onClose}
                         aria-label="Свернуть плеер"><IcoChevL style={{ transform: 'rotate(-90deg)' }} /></motion.button>
        </div>

        <motion.div className="np-covwrap" layoutId="np-cover" transition={sharedCover}>
          <FadeImg className={'np-cover' + (st.playing ? ' kb' : '')} src={coverOr(cur)} alt="" />
        </motion.div>

        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <AnimatePresence mode="popLayout" initial={false}>
              <motion.div
                key={cur.id}
                variants={trackVariants}
                initial="hidden"
                animate="visible"
                exit="exit"
              >
                <div className="np-tname">{cur.title}</div>
                <div className="np-tartist">{cur.artist}</div>
                {cur.album && <div className="pl-artist" style={{ marginTop: 2 }}>{cur.album}{cur.year ? ' · ' + cur.year : ''}</div>}
              </motion.div>
            </AnimatePresence>
          </div>
          <HeartBtn id={cur.id} big />
          <button className="iconact" aria-label="Скачать в чат" onClick={download}>
            <AnimatedIcon name={dlBusy ? 'busy' : dlDone ? 'done' : 'idle'}>
              {dlBusy ? <span className="spinner" style={{ width: 15, height: 15 }} />
                : dlDone ? <IcoCheck width="1em" height="1em" /> : <IcoDl width="1em" height="1em" />}
            </AnimatedIcon>
          </button>
        </div>
        {dl && (
          <motion.div className="np-chip" initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }}>
            <span className="np-dot" /> {dl}
          </motion.div>
        )}

        {st.error && <div className="err" style={{ marginTop: 12 }}>{st.error}</div>}

        <div className={'np-chip' + (st.kind === 'preview' ? ' np-chip-dim' : '')}>
          <span className="np-dot" />
          {st.kind === 'preview' ? 'Превью 0:30 · кнопка ⤓ доставит полный трек в чат'
            : st.kind === 'file' ? 'Играет из Telegram · полный трек'
            : 'Полный трек онлайн'}
        </div>

        <div className="seekrow" style={{ marginTop: 18 }}>
          <Seek showTime timeClass="ttime" ariaLabel="Позиция" />
        </div>

        <div className="np-btns">
          <motion.button className="cbtn glass" whileTap={{ scale: .93 }} aria-label="Предыдущий"
                         onClick={() => { haptic('light'); P.prev(); }}><IcoPrev /></motion.button>
          <motion.button className="cbtn big" whileTap={{ scale: .9 }} aria-label="Играть/пауза"
                         onClick={() => { haptic('medium'); P.toggle(); }}>
            <PlayPause playing={st.playing} loading={st.loading} big />
          </motion.button>
          <motion.button className="cbtn glass" whileTap={{ scale: .93 }} aria-label="Следующий"
                         onClick={() => { haptic('light'); P.next(); }}><IcoNext /></motion.button>
        </div>

        <div className="minirow">
          <button className={'cbtn' + (st.shuffle ? ' on' : '')} aria-label="Перемешать"
                  onClick={() => { haptic('light'); P.setShuffle(!st.shuffle); }}><IcoShuffle /></button>
          <button className={'cbtn' + (st.loop ? ' on' : '')} aria-label="Повтор"
                  onClick={() => { haptic('light'); P.setLoop(!st.loop); }}><IcoRepeat /></button>
        </div>

        {/* громкость: на телефоне десктопная панель скрыта, поэтому
            регулятор живёт прямо в «Сейчас играет» */}
        <div className="np-vol">
          <button className="cbtn" aria-label="Звук вкл/выкл"
                  onClick={() => { haptic('light'); P.toggleMute(); }}>
            {st.volume > 0 ? <IcoVol /> : <IcoVolOff />}
          </button>
          <input className="seek np-vol-range" type="range" min="0" max="100"
                 value={Math.round(st.volume * 100)}
                 style={{ '--fill': Math.round(st.volume * 100) + '%' }}
                 aria-label="Громкость"
                 onChange={e => P.setVolume(+e.target.value / 100)} />
          <span className="np-vol-pct">{Math.round(st.volume * 100)}%</span>
        </div>

        <LyricsView track={cur} st={st} />

        <div className="np-queue">
          <div className="npq-cap">Очередь · {st.queue.length}</div>
          <AnimatePresence initial={false}>
            {st.queue.map((t, i) => (
              <motion.button
                key={t.id + ':' + i}
                className={'npq' + (i === st.index ? ' on' : '')}
                variants={queueVariants}
                custom={i}
                initial={i < 14 ? 'hidden' : false}
                animate="visible"
                exit="exit"
                onClick={() => { haptic('select'); P.play(st.queue, i, null, P.inWave()); }}
              >
                {i === st.index && (
                  <motion.span layoutId="npq-active" className="npq-bg" transition={player.queue} />
                )}
                <span className="npq-n">{i === st.index ? (st.playing ? <Eq /> : '▶') : i + 1}</span>
                <span className="npq-t">{t.title} — {t.artist}</span>
              </motion.button>
            ))}
          </AnimatePresence>
        </div>
      </div>
    </motion.div>
  );
}

async function downloadJob(track, setDl) {
  setDl('в очереди');
  try {
    const { job } = await api('/api/download?track_id=' + track.id, { method: 'POST' });
    let it = 0;
    while (it++ < 175) {
      const s = await api('/api/jobs/' + job);
      if (s.status === 'done') { setDl('✓ файл отправлен в чат'); return '✓ файл отправлен в чат'; }
      if (s.status === 'error') { setDl('Не получилось · попробуй ещё раз'); return 'ошибка'; }
      setDl('⬇ ' + Math.round((s.progress || 0) * 100) + '% · готовим файл');
      await new Promise(r => setTimeout(r, 1200));
    }
    setDl('');
    return '';
  } catch (e) {
    const msg = e.message || 'Ошибка сети';
    if (msg === 'telegram_required') {
      setDl('Нужен вход в Telegram');
      return 'telegram_required';
    }
    setDl(msg);
    return msg;
  }
}

/* ---------------- вход по коду из /code ---------------- */
function LoginSheet({ open, onClose }) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    setErr('');
    const t = setTimeout(() => ref.current?.focus(), 180);
    return () => clearTimeout(t);
  }, [open]);

  const digits = code.replace(/\D/g, '');
  const submit = async () => {
    if (digits.length !== 8 || busy) return;
    setBusy(true);
    setErr('');
    try {
      await exchangeCode(digits);
      toast('Готово — открываю аккаунт Telegram', { kind: 'success' });
      setTimeout(() => location.reload(), 350);
    } catch (e) {
      setErr(e.message || 'Код не подошёл');
      setBusy(false);
    }
  };

  return (
    <Sheet open={open} onClose={onClose} labelledBy="login-title">
      <div className="sht" id="login-title">Вход в аккаунт Telegram</div>
      <p className="muted" style={{ margin: '0 0 12px', lineHeight: 1.5 }}>
        В браузере приложение работает как гость: слушать можно, а отправлять
        треки в чат — нет. Открой бота, отправь <b>/code</b> и введи 8 цифр.
      </p>
      <a className="bigbtn" href={BOT_URL} target="_blank" rel="noopener"
         style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
                  gap: 8, textDecoration: 'none', marginBottom: 12 }}>
        📱 Открыть бота и получить код
      </a>
      <input
        ref={ref}
        className="shinput"
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={9}
        placeholder="1234 5678"
        value={code}
        onChange={e => setCode(e.target.value.replace(/[^\d ]/g, ''))}
        onKeyDown={e => { if (e.key === 'Enter') submit(); }}
      />
      {err && <div className="err" style={{ marginTop: 10 }}>{err}</div>}
      <div className="btnrow">
        <button className="bigbtn" onClick={onClose}>Позже</button>
        <button className="bigbtn primary" disabled={digits.length !== 8 || busy}
                onClick={submit}>
          {busy ? <span className="minispinner" /> : 'Войти'}
        </button>
      </div>
    </Sheet>
  );
}
