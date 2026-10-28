/* Vibe Music v2 — корневое приложение: табы, тихая авторизация, мини-плеер.
   Премиум-полиш: spring-переходы, индикатор таб-бара, haptic, Ken-Burns обложка. */
import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { api, API_BASE, ensureGuestToken, hasAuth, setUnauthorizedHandler } from './api.js';
import { setAuthToken } from './store.js';
import { firstUserName, haptic } from './tg.js';
import * as P from './player.js';
import Library from './screens/Library.jsx';
import SearchScreen from './screens/Search.jsx';
import Playlists from './screens/Playlists.jsx';
import ImportScreen from './screens/Import.jsx';

const TABS = [
  { id: 'library', icon: '♪', label: 'Музыка' },
  { id: 'search', icon: '⌕', label: 'Поиск' },
  { id: 'playlists', icon: '▤', label: 'Плейлисты' },
  { id: 'import', icon: '⇥', label: 'Импорт' },
];

/* Тихая авторизация ТЕКУЩЕГО контекста (используется и при загрузке,
   и когда серверная 401 сообщила, что сессия протухла):
   1) initData Telegram (ждём до ~1.2 c, если SDK поздний)
   2) иначе — молчаливый гость по device_id (чистый браузер, БЕЗ кодов) */
const sleep = ms => new Promise(r => setTimeout(r, ms));

export default function App() {
  const [tab, setTab] = useState('library');
  const [ok, setOk] = useState('boot');
  const [name, setName] = useState('');
  const [playingTrack, setPlayingTrack] = useState(null);

  useEffect(() => {
    let dead = false;
    const boot = async () => {
      // 1) Telegram: initData обычно есть сразу; на всякий случай до ~1.2 c
      for (let i = 0; i < 12 && !hasAuth() && !dead; i++) await sleep(100);
      // 2) не Telegram: тихий гость (попытки с паузой — туннель мог моргнуть)
      for (let t = 0; t < 6 && !hasAuth() && !dead; t++) {
        if (t) await sleep(2500);
        try { await ensureGuestToken(); } catch { /* сеть ещё не готова */ }
      }
      if (dead) return;
      if (hasAuth()) { setOk(true); setName(firstUserName() || 'Гость'); }
      else setOk('boot');            // splash остаётся; 401-хук перезапустит boot
    };
    setUnauthorizedHandler(() => {
      setAuthToken('');              // сессия протухла — тихо получим новую
      boot();
    });
    boot();
    const t0 = current();
    if (t0) setPlayingTrack(t0);
    return () => { dead = true; };
  }, []);

  const switchTab = (id) => {
    if (id === tab) return;
    haptic('soft');
    setTab(id);
  };

  return (
    <div className="app">
      <TopBar name={name} />
      {ok === true
        ? <>
          {/* key={tab} — ремоunt экрана, launch view-rise transition */}
          <main className="screen">
            <div className="vwrap" key={tab}>
              {tab === 'library' && <Library />}
              {tab === 'search' && <SearchScreen />}
              {tab === 'playlists' && <Playlists />}
              {tab === 'import' && <ImportScreen onOpenPlaylist={() => switchTab('playlists')} />}
            </div>
          </main>
          <PlayerBar onExpand={setPlayingTrack} />
        </>
        : <BootSplash />}
      {playingTrack && <NowPlaying track={playingTrack} onClose={() => setPlayingTrack(null)} />}
      <TabBar tab={tab} setTab={switchTab} />
    </div>
  );
}

function current() { return P.getState().queue[P.getState().index]; }

/* ---------------- top bar ---------------- */
function TopBar({ name }) {
  return (
    <header className="topbar">
      <div className="brand"><span className="logo">◈</span> Vibe</div>
      <div className="user">{name || ''}</div>
    </header>
  );
}

/* ---------------- тихая загрузка вместо экрана кода ---------------- */
function BootSplash() {
  return (
    <div className="cardbox">
      <div className="login">
        <div className="boot-eq"><i /><i /><i /></div>
        <h2>Загрузка…</h2>
        <p className="muted">Подключаюсь к серверу музыки</p>
      </div>
    </div>
  );
}

/* ---------------- tab bar (плавный индикатор + haptic) ---------------- */
function TabBar({ tab, setTab }) {
  const idx = TABS.findIndex(t => t.id === tab);
  return (
    <nav className="tabbar">
      <div className="tabind" style={{ transform: `translateX(${idx * 100}%)` }} />
      {TABS.map(t => (
        <button key={t.id} className={'tab' + (tab === t.id ? ' on' : '')}
                onClick={() => setTab(t.id)}>
          <span className="ti">{t.icon}</span><span>{t.label}</span>
        </button>
      ))}
    </nav>
  );
}

/* ---------------- мини плеер + полноэкранный now playing ---------------- */
function usePlayerState() {
  return useSyncExternalStore(P.subscribe, P.getState, P.getState);
}

function PlayerBar({ onExpand }) {
  const st = usePlayerState();
  const cur = st.queue[st.index];
  if (!cur) return null;
  const pct = st.duration ? (st.currentTime / st.duration) * 100 : 0;
  return (
    <div className="pbar" onClick={() => onExpand(cur)}>
      <div className="pbar-line" style={{ width: pct + '%' }} />
      <div className="pcovwrap">
        <img className="pbar-cover" src={coverOr(cur)} alt="" />
        {st.playing && <span className="eq"><i /><i /><i /></span>}
      </div>
      <div className="pbar-meta" key={cur.id}>
        <div className="pbar-title">{cur.title}</div>
        <div className="pbar-artist">{cur.artist}</div>
      </div>
      <button className="pib" onClick={e => { e.stopPropagation(); haptic('light'); P.prev(); }}>⏮</button>
      <button className="pib key-pop" key={String(st.playing)} onClick={e => { e.stopPropagation(); haptic('light'); P.toggle(); }}>
        {st.playing ? '⏸' : '▶'}
      </button>
      <button className="pib" onClick={e => { e.stopPropagation(); haptic('light'); P.next(); }}>⏭</button>
    </div>
  );
}

function NowPlaying({ track, onClose }) {
  const st = usePlayerState();
  const cur = st.queue[st.index] || track;
  const pct = st.duration ? (st.currentTime / st.duration) * 100 : 0;
  const playState = st.loading ? 'load' : (st.playing ? 'play' : 'pause');
  return (
    <div className="np" onClick={onClose}>
      <div className="np-card" onClick={e => e.stopPropagation()}>
        <div className="np-close" onClick={onClose}>⌄</div>
        {st.error && <div className="err">{st.error}</div>}
        <div className="np-art">
          <div className="np-aura" />
          <img className={'np-cover' + (st.playing ? ' kb' : '')} src={coverOr(cur)} alt="" />
          {st.playing && <span className="eq eq-lg"><i /><i /><i /></span>}
        </div>
        <div className="np-meta" key={cur.id}>
          <div className="np-title">{cur.title}</div>
          <div className="np-artist">{cur.artist}</div>
          {!cur.ready && <div className="np-chip"><span className="np-dot" />Онлайн-превью · 0:30</div>}
        </div>
        <div className="seekrow" onClick={e => e.stopPropagation()}>
          <span className="np-time">{P.fmt(st.currentTime)}</span>
          <input className="seek" type="range" min="0" max="1000"
                 value={Math.round(pct * 10)}
                 style={{ '--fill': pct.toFixed(1) + '%' }}
                 onChange={e => P.seek(+e.target.value / 1000)} />
          <span className="np-time">{P.fmt(st.duration)}</span>
        </div>
        <div className="np-btns">
          <button onClick={() => { haptic('light'); P.setShuffle(!st.shuffle); }}
                  className={'ib toggle' + (st.shuffle ? ' on' : '')}>🔀</button>
          <button onClick={() => { haptic('light'); P.prev(); }} className="ib big">⏮</button>
          <button onClick={() => { haptic('medium'); P.toggle(); }}
                  className={'ib big play st-' + playState} key={playState}>
            {st.loading ? <span className="spinner" /> : (st.playing ? '⏸' : '▶')}
          </button>
          <button onClick={() => { haptic('light'); P.next(); }} className="ib big">⏭</button>
          <button onClick={() => { haptic('light'); P.setLoop(!st.loop); }}
                  className={'ib toggle' + (st.loop ? ' on' : '')}>🔁</button>
        </div>
        <div className="np-queue">
          {st.queue.map((t, i) => (
            <button key={t.id} className={'npq' + (i === st.index ? ' on' : '')}
                    style={{ animationDelay: Math.min(i, 12) * 30 + 'ms' }}
                    onClick={() => { haptic('select'); P.play(st.queue, i); }}>
              <span className="npq-n">{i === st.index
                ? (st.playing ? <span className="eq eq-sm"><i /><i /><i /></span> : '▶')
                : (i + 1)}</span>
              <span className="npq-t">{t.title} — {t.artist}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function coverOr(t) {
  if (t && t.cover_url && t.cover_url.startsWith('http')) return t.cover_url;
  if (t && t.cover_url) return API_BASE + t.cover_url;
  return 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg"/%3E';
}

/* Премиум-скелетон загрузки (shimmer) — для списков и сеток плейлистов. */
export function Skeletons({ n = 7, grid = false }) {
  if (grid) return (
    <div className="plgrid">
      {Array.from({ length: n }).map((_, i) => (
        <div key={i}>
          <div className="skel plsk" style={{ animationDelay: (i % 4) * 90 + 'ms' }} />
          <div className="skel sk-line" style={{ animationDelay: (i % 4) * 90 + 'ms' }} />
        </div>
      ))}
    </div>
  );
  return (
    <div className="list">
      {Array.from({ length: n }).map((_, i) => (
        <div className="skelrow" key={i} style={{ animationDelay: i * 70 + 'ms' }}>
          <div className="skel covsk" />
          <div className="skelmeta">
            <div className="skel sk-line w76" />
            <div className="skel sk-line w44" />
          </div>
        </div>
      ))}
    </div>
  );
}

export { coverOr };
