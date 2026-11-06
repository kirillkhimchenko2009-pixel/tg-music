/* Vibe Music v3 — премиальная оболочка: сайдбар (десктоп), топбар,
   нижний плеер-бар, полноэкранный нау-плей, таб-бар (мобайл).
   Маршруты живут в стек-роутере nav.js (назад/вперёд/buttons). */
import React, { useEffect, useState } from 'react';
import * as P from './player.js';
import { api, API_BASE, ensureGuestToken, hasAuth, setUnauthorizedHandler } from './api.js';
import { loadLikes, useLikes } from './likes.js';
import { setAuthToken, initDataRaw } from './store.js';
import { firstUserName, haptic } from './tg.js';
import { navInit, navPush, navBack, navFwd, useNav, useNavMeta } from './nav.js';
import {
  coverOr, HeartBtn, PlayPause, fmtTime, gradFor, dominantColor,
  IcoHome, IcoWave, IcoSearch, IcoLib, IcoImport, IcoPodcast,
  IcoChevL, IcoChevR, IcoBell, IcoX, IcoCheck,
  IcoPrev, IcoNext, IcoShuffle, IcoRepeat, IcoVol, IcoVolOff,
  IcoExpand, IcoDl, IcoHeart, IcoClock, IcoNote,
} from './ui.jsx';

import Home from './screens/Home.jsx';
import Collection, { PlaylistScreen } from './screens/Collection.jsx';
import SearchScreen from './screens/Search.jsx';
import ImportScreen from './screens/Import.jsx';
import Podcasts from './screens/Podcasts.jsx';
import Artist from './screens/Artist.jsx';
import Album from './screens/Album.jsx';
import Wave from './screens/Wave.jsx';

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
  { tab: 'import', icon: IcoImport, label: 'Импорт' },
  { tab: 'podcasts', icon: IcoPodcast, label: 'Подкасты', soon: true },
];

const TABS_M = [
  { id: 'home', icon: IcoHome, label: 'Главная' },
  { id: 'wave', icon: IcoWave, label: 'Волна' },
  { id: 'search', icon: IcoSearch, label: 'Поиск' },
  { id: 'collection', icon: IcoLib, label: 'Коллекция' },
  { id: 'import', icon: IcoImport, label: 'Импорт' },
];

export default function App() {
  const view = useNav();
  const [ok, setOk] = useState('boot');
  const [name, setName] = useState('');
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (location.hash) history.replaceState(null, '', location.pathname + location.search);
    let dead = false;
    const boot = async () => {
      for (let i = 0; i < 12 && !hasAuth() && !dead; i++) await sleep(100);
      for (let t = 0; t < 6 && !hasAuth() && !dead; t++) {
        if (t) await sleep(2500);
        try { await ensureGuestToken(); } catch { /* сеть ещё не готова */ }
      }
      if (dead) return;
      if (hasAuth()) { setOk(true); setName(firstUserName() || 'Гость'); }
      else setOk('boot');
      loadLikes();
    };
    navInit(startView());
    setUnauthorizedHandler(() => {
      setAuthToken('');
      boot();
    });
    boot();
    return () => { dead = true; };
  }, []);

  if (ok !== true) return <BootSplash />;

  return (
    <div className="app">
      <Sidebar view={view} />
      <div className="content">
        <TopBar name={name} />
        <main className="page" key={viewKey(view)}>
          <Screen view={view} />
        </main>
      </div>
      <PlayerBar onExpand={() => setExpanded(true)} />
      {expanded && <NowPlaying onClose={() => setExpanded(false)} />}
      <TabBar tab={view} />
    </div>
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
    <button className={'side-item' + (on ? ' on' : '')}
            onClick={() => { haptic('soft'); navPush({ tab: item.tab, sub: item.sub || null }); }}>
      <span className="si"><Ico width="1em" height="1em" /></span>
      <span>{item.label}</span>
      {item.soon && <span className="badge-dot" />}
    </button>
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
        {pop === 'bell' && (
          <div className="pop" onClick={e => e.stopPropagation()}>
            <div className="pop-head"><div className="pop-title">Уведомления</div></div>
            <div className="pop-empty">Пока тихо: релизы и новости появятся здесь позже.</div>
          </div>
        )}
      </div>

      <div className="popwrap">
        <button className="tb-user" onClick={e => { e.stopPropagation(); setPop(p => p === 'user' ? null : 'user'); }}>
          <span className="tb-ava">{(name || 'Г').slice(0, 1).toUpperCase()}</span>
          <span className="tb-name">{name}</span>
        </button>
        {pop === 'user' && (
          <div className="pop" onClick={e => e.stopPropagation()}>
            <div className="pop-head">
              <div className="pop-title">{name || 'Гость'}</div>
              <div className="pop-sub">{initDataRaw.value ? 'Аккаунт Telegram' : 'Гостевая сессия этого устройства'}</div>
            </div>
            <button className="pop-item" onClick={() => { navPush({ tab: 'collection', sub: 'likes' }); setPop(null); }}>
              <IcoHeart width="1em" height="1em" /> Любимое
            </button>
            <button className="pop-item" onClick={() => { navPush({ tab: 'collection', sub: 'history' }); setPop(null); }}>
              <IcoClock width="1em" height="1em" /> История
            </button>
          </div>
        )}
      </div>
    </header>
  );
}

/* ---------------- boot ---------------- */
function BootSplash() {
  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: 'var(--bg)' }}>
      <div className="cardbox" style={{ width: 'min(88vw, 340px)' }}>
        <div className="login">
          <div className="boot-eq"><i /><i /><i /></div>
          <h2>Vibe запускается…</h2>
          <p className="muted">Подключаюсь к музыкальному серверу</p>
        </div>
      </div>
    </div>
  );
}

/* ---------------- таб-бар (мобайл) ---------------- */
function TabBar({ tab }) {
  const on = tab.tab || 'home';
  return (
    <nav className="tabbar">
      {TABS_M.map(t => {
        const Ico = t.icon;
        return (
          <button key={t.id} className={'tab' + (on === t.id ? ' on' : '')}
                  onClick={() => { haptic('soft'); navPush({ tab: t.id, sub: null }); }}>
            <Ico width="1em" height="1em" />
            <span>{t.label}</span>
          </button>
        );
      })}
    </nav>
  );
}

/* ---------------- плеер-бар ---------------- */
function PlayerBar({ onExpand }) {
  const st = P.usePlayerSnap();
  const cur = st.queue[st.index];
  if (!cur) return null;
  const pct = st.duration ? (st.currentTime / st.duration) * 100 : 0;
  return (
    <div className="pbar">
      <div className="pcovwrap" onClick={onExpand}>
        <img src={coverOr(cur) || undefined} alt="" onError={e => { e.currentTarget.style.visibility = 'hidden'; }} />
        {st.playing && <span className="eq"><i /><i /><i /></span>}
      </div>

      <div className="pl-meta" onClick={onExpand}>
        <div className="pl-title">{cur.title}</div>
        <div className="pl-artist">{cur.artist}</div>
      </div>

      <div className="pl-heart" onClick={e => e.stopPropagation()}><HeartBtn id={cur.id} /></div>

      {/* десктоп: центр — прогресс + контролы */}
      <div className="pl-omni" onClick={e => e.stopPropagation()}>
        <div className="pl-lines">
          <span>{P.fmt(st.currentTime)}</span>
          <input className="seek" type="range" min="0" max="1000" value={Math.round(pct * 10)}
                 style={{ '--fill': pct.toFixed(1) + '%' }}
                 aria-label="Позиция трека"
                 onChange={e => P.seek(+e.target.value / 1000)} />
          <span>{P.fmt(st.duration)}</span>
        </div>
        <div className="pl-center">
          <button className={'pl-ctl' + (st.shuffle ? ' on' : '')} aria-label="Перемешать"
                  onClick={() => { haptic('light'); P.setShuffle(!st.shuffle); }}><IcoShuffle /></button>
          <button className="pl-ctl" aria-label="Предыдущий" onClick={() => { haptic('light'); P.prev(); }}><IcoPrev /></button>
          <button className="pl-play" aria-label="Играть/пауза" onClick={() => { haptic('medium'); P.toggle(); }}>
            <PlayPause playing={st.playing} loading={st.loading} />
          </button>
          <button className="pl-ctl" aria-label="Следующий" onClick={() => { haptic('light'); P.next(); }}><IcoNext /></button>
          <button className={'pl-ctl' + (st.loop ? ' on' : '')} aria-label="Повтор"
                  onClick={() => { haptic('light'); P.setLoop(!st.loop); }}><IcoRepeat /></button>
        </div>
      </div>

      {/* мобильный: компактные контролы */}
      <div className="pl-mctl" onClick={e => e.stopPropagation()}>
        <button className="pl-ctl" aria-label="Предыдущий" onClick={() => { haptic('light'); P.prev(); }}><IcoPrev /></button>
        <button className="pl-play" aria-label="Играть/пауза" onClick={() => { haptic('medium'); P.toggle(); }}>
          <PlayPause playing={st.playing} loading={st.loading} />
        </button>
        <button className="pl-ctl" aria-label="Следующий" onClick={() => { haptic('light'); P.next(); }}><IcoNext /></button>
      </div>

      {/* десктоп-право: громкость + раскрыть */}
      <div className="pl-right" onClick={e => e.stopPropagation()}>
        <div className="vol">
          <button className="pl-ctl" aria-label="Звук вкл/выкл"
                  onClick={() => P.setVolume(P.getState().volume > 0 ? 0 : 1)}>
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
    </div>
  );
}

/* ---------------- нау-плей (полноэкранный) ---------------- */
function NowPlaying({ onClose }) {
  const st = P.usePlayerSnap();
  const cur = st.queue[st.index];
  const [dl, setDl] = useState('');
  const [glow, setGlow] = useState(null);

  useEffect(() => {
    const esc = e => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [onClose]);

  useEffect(() => {
    let dead = false;
    dominantColor(coverOr(cur)).then(rgb => {
      if (!dead && rgb) setGlow(`rgba(${rgb[0]},${rgb[1]},${rgb[2]},.55)`);
    });
    return () => { dead = true; };
  }, [cur?.id]);

  if (!cur) return null;

  const download = async () => {
    if (!cur || dl) return;
    haptic('medium');
    await downloadJob(cur, setDl);
    setTimeout(() => setDl(''), 4000);
  };

  return (
    <div className="np"
         style={{ '--g': glow ? `radial-gradient(120% 90% at 50% -8%, ${glow}, transparent 68%)` : undefined }}>
      <div className="np-in">
        <div className="np-head">
          <span className="np-kicker">Сейчас играет</span>
          <button className="np-x" onClick={onClose} aria-label="Свернуть плеер"><IcoChevL style={{ transform: 'rotate(-90deg)' }} /></button>
        </div>

        <div className="np-covwrap">
          <img className={'np-cover' + (st.playing ? ' kb' : '')} src={coverOr(cur) || undefined} alt="" />
        </div>

        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div className="np-tname">{cur.title}</div>
            <div className="np-tartist">{cur.artist}</div>
            {cur.album && <div className="pl-artist" style={{ marginTop: 2 }}>{cur.album}{cur.year ? ' · ' + cur.year : ''}</div>}
          </div>
          <HeartBtn id={cur.id} big />
          <button className="iconact" aria-label="Скачать в чат" onClick={download}>
            {dl && !dl.startsWith('✓') ? <span className="spinner" style={{ width: 15, height: 15 }} />
              : (dl ? <IcoCheck width="1em" height="1em" /> : <IcoDl width="1em" height="1em" />)}
          </button>
        </div>
        {dl && <div className="np-chip"><span className="np-dot" /> {dl}</div>}

        {st.error && <div className="err" style={{ marginTop: 12 }}>{st.error}</div>}

        <div className={'np-chip' + (st.kind === 'preview' ? ' np-chip-dim' : '')}>
          <span className="np-dot" />
          {st.kind === 'preview' ? 'Превью 0:30 · кнопка ⤓ доставит полный трек в чат'
            : st.kind === 'file' ? 'Играет из Telegram · полный трек'
            : 'Полный трек онлайн'}
        </div>

        <div className="seekrow" style={{ marginTop: 18 }}>
          <span className="ttime">{P.fmt(st.currentTime)}</span>
          <input className="seek" type="range" min="0" max="1000"
                 value={Math.round((st.duration ? st.currentTime / st.duration * 100 : 0) * 10)}
                 style={{ '--fill': st.duration ? (st.currentTime / st.duration * 100).toFixed(1) + '%' : '0%' }}
                 aria-label="Позиция"
                 onChange={e => P.seek(+e.target.value / 1000)} />
          <span className="ttime">{P.fmt(st.duration)}</span>
        </div>

        <div className="np-btns">
          <button className="cbtn glass" aria-label="Предыдущий" onClick={() => { haptic('light'); P.prev(); }}><IcoPrev /></button>
          <button className="cbtn big" aria-label="Играть/пауза" onClick={() => { haptic('medium'); P.toggle(); }}>
            <PlayPause playing={st.playing} loading={st.loading} big />
          </button>
          <button className="cbtn glass" aria-label="Следующий" onClick={() => { haptic('light'); P.next(); }}><IcoNext /></button>
        </div>

        <div className="minirow">
          <button className={'cbtn' + (st.shuffle ? ' on' : '')} aria-label="Перемешать"
                  onClick={() => { haptic('light'); P.setShuffle(!st.shuffle); }}><IcoShuffle /></button>
          <button className={'cbtn' + (st.loop ? ' on' : '')} aria-label="Повтор"
                  onClick={() => { haptic('light'); P.setLoop(!st.loop); }}><IcoRepeat /></button>
        </div>

        <div className="np-queue">
          <div className="npq-cap">Очередь · {st.queue.length}</div>
          {st.queue.map((t, i) => (
            <button key={t.id} className={'npq' + (i === st.index ? ' on' : '')}
                    style={{ animationDelay: Math.min(i, 12) * 30 + 'ms' }}
                    onClick={() => { haptic('select'); P.play(st.queue, i); }}>
              <span className="npq-n">{i === st.index ? (st.playing ? <span className="eq"><i /><i /><i /></span> : '▶') : i + 1}</span>
              <span className="npq-t">{t.title} — {t.artist}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

async function downloadJob(track, setDl) {
  setDl('в очереди');
  try {
    const { job } = await api('/api/download?track_id=' + track.id, { method: 'POST' });
    let it = 0;
    while (it++ < 175) {
      const s = await api('/api/jobs/' + job);
      if (s.status === 'done') { setDl('✓ файл отправлен в чат'); return; }
      if (s.status === 'error') { setDl('Не получилось · попробуй ещё раз'); return; }
      setDl('⬇ ' + Math.round((s.progress || 0) * 100) + '% · готовим файл');
      await new Promise(r => setTimeout(r, 1200));
    }
    setDl('');
  } catch {
    setDl('Ошибка сети');
  }
}
