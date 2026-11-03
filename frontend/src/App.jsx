/* Vibe Music v2 — корневое приложение: табы, тихая авторизация, мини-плеер.
   Премиум-полиш: spring-переходы, индикатор таб-бара, haptic, Ken-Burns обложка. */
import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { api, API_BASE, ensureGuestToken, hasAuth, setUnauthorizedHandler } from './api.js';
import { toggleLike, useLikes } from './likes.js';
import { setAuthToken } from './store.js';
import { firstUserName, haptic } from './tg.js';
import { loadLikes } from './likes.js';
import * as P from './player.js';
import Wave from './screens/Wave.jsx';
import Library from './screens/Library.jsx';
import SearchScreen from './screens/Search.jsx';
import Playlists from './screens/Playlists.jsx';
import ImportScreen from './screens/Import.jsx';

const TABS = [
  { id: 'wave', icon: '≋', label: 'Волна' },
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
      loadLikes();
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
          <main className={'screen' + (tab === 'wave' ? ' wavescreen' : '')}>
            <div className="vwrap" key={tab}>
              {tab === 'wave' && <Wave />}
              {tab === 'library' && <Library />}
              {tab === 'search' && <SearchScreen />}
              {tab === 'playlists' && <Playlists onImport={() => switchTab('import')} />}
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
      <button className="pib" aria-label="Предыдущий" onClick={e => { e.stopPropagation(); haptic('light'); P.prev(); }}><IcoPrev /></button>
      <button className="pib" aria-label="Играть/пауза" onClick={e => { e.stopPropagation(); haptic('light'); P.toggle(); }}>
        <PlayPause playing={st.playing} />
      </button>
      <button className="pib" aria-label="Следующий" onClick={e => { e.stopPropagation(); haptic('light'); P.next(); }}><IcoNext /></button>
    </div>
  );
}

function NowPlaying({ track, onClose }) {
  const st = usePlayerState();
  const cur = st.queue[st.index] || track;
  const pct = st.duration ? (st.currentTime / st.duration) * 100 : 0;
  const [dl, setDl] = useState('');
  const download = async () => {
    if (dl === 'busy') return;
    haptic('medium'); setDl('busy');
    try {
      const { job } = await api('/api/download?track_id=' + cur.id, { method: 'POST' });
      let it = 0;
      while (it++ < 400) {
        const s2 = await api('/api/jobs/' + job);
        if (s2.status === 'done') { setDl('done'); break; }
        if (s2.status === 'error') { setDl('err'); break; }
        await new Promise(r => setTimeout(r, 1500));
      }
    } catch { setDl('err'); }
    setTimeout(() => setDl(''), 4000);
  };
  const label = (cur.title || '') + ' — ' + (cur.artist || '');
  return (
    <div className="np" onClick={onClose}>
      <div className="np-card" onClick={e => e.stopPropagation()}>
        <div className="blobs">
          <span className="blob blob-1" /><span className="blob blob-2" /><span className="blob blob-3" />
        </div>
        <div className="np-body">
          <div className="np-close" onClick={onClose}>⌄</div>
          {st.error && <div className="err">{st.error}</div>}
          <div className="np-float">
            <div className="covin" key={cur.id}>
              <img className={'np-cover' + (st.playing ? ' kb' : '')} src={coverOr(cur)} alt="" />
              {st.playing && <span className="eq eq-lg"><i /><i /><i /></span>}
            </div>
          </div>
          <div className="pillbar" key={cur.id}>
            <HeartBtn id={cur.id} />
            <div className="mq">
              <div className="mq-in">
                <span>{label}</span><span aria-hidden="true">{label}</span>
              </div>
            </div>
            <button className="cbtn" aria-label="Скачать в чат" onClick={download}>
              {dl === 'busy' ? <span className="minispinner" /> : (dl === 'done' ? '✓' : <IcoDownload />)}
            </button>
          </div>
          {/* что именно играет: скачанное / полный онлайн / 30-сек превью */}
          <div className={'np-chip' + (st.kind === 'preview' ? ' np-chip-dim' : '')}>
            <span className="np-dot" />
            {st.kind === 'preview' ? 'Превью 0:30 · жми ⤓ для полного трека'
              : st.kind === 'file' ? 'Играет из Telegram · полный трек'
              : 'Полный трек онлайн · без скачивания'}
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
            <button onClick={() => { haptic('light'); P.prev(); }} className="cbtn glass" aria-label="Предыдущий"><IcoPrev /></button>
            <button onClick={() => { haptic('medium'); P.toggle(); }}
                    className={'cbtn big' + (st.playing ? ' playing' : '')} aria-label="Играть/пауза">
              <PlayPause playing={st.playing} loading={st.loading} big />
            </button>
            <button onClick={() => { haptic('light'); P.next(); }} className="cbtn glass" aria-label="Следующий"><IcoNext /></button>
          </div>
          <div className="minirow">
            <button onClick={() => { haptic('light'); P.setShuffle(!st.shuffle); }}
                    className={'cbtn' + (st.shuffle ? ' on' : '')} aria-label="Перемешать"><IcoShuffle /></button>
            <button onClick={() => { haptic('light'); P.setLoop(!st.loop); }}
                    className={'cbtn' + (st.loop ? ' on' : '')} aria-label="Повтор"><IcoRepeat /></button>
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
    </div>
  );
}

function coverOr(t) {
  if (t && t.cover_url && t.cover_url.startsWith('http')) return t.cover_url;
  if (t && t.cover_url) return API_BASE + t.cover_url;
  return 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg"/%3E';
}

/* ---------------- морф play/pause (как в Яндекс.Музыке) ---------------- */
export function PlayPause({ playing, loading, big }) {
  return (
    <span className={'ppm' + (big ? ' lg' : '') + (playing ? ' on' : '')}>
      {loading
        ? <span className="spinner spinner-dark" />
        : <>
          <svg className="pp-ico" viewBox="0 0 24 24"><path d="M8.2 5.6v12.8L18.6 12z" /></svg>
          <svg className="pp-ico" viewBox="0 0 24 24">
            <rect x="6.6" y="5.4" width="3.6" height="13.2" rx="1.7" />
            <rect x="13.8" y="5.4" width="3.6" height="13.2" rx="1.7" />
          </svg>
        </>}
    </span>
  );
}

/* ---------------- сердечко «в Любимое» ---------------- */
const HEART_D = ('M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3'
  + 'c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5'
  + 'c0 3.78-3.4 6.86-8.55 11.54L12 21.35z');
export function HeartBtn({ id, big }) {
  const liked = useLikes().has(id);
  return (
    <button className={'heart' + (liked ? ' on' : '') + (big ? ' lg' : '')}
            aria-label="В любимое"
            onClick={e => { e.stopPropagation(); haptic(liked ? 'soft' : 'light'); toggleLike(id); }}>
      <svg viewBox="0 0 24 24"><path d={HEART_D} /></svg>
    </button>
  );
}

/* ---------------- SVG-иконки: кнопки в едином стиле ЯМ ---------------- */
const STROKE = {
  viewBox: '0 0 24 24', className: 'ico', fill: 'none', stroke: 'currentColor',
  strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round',
};
export function IcoPrev() {
  return (
    <svg {...STROKE}>
      <path d="M18.4 5.4v13.2L7.4 12z" fill="currentColor" stroke="none" />
      <rect x="4" y="5.4" width="2.4" height="13.2" rx="1.2" fill="currentColor" stroke="none" />
    </svg>
  );
}
export function IcoNext() {
  return (
    <svg {...STROKE}>
      <path d="M5.6 5.4v13.2L16.6 12z" fill="currentColor" stroke="none" />
      <rect x="17.6" y="5.4" width="2.4" height="13.2" rx="1.2" fill="currentColor" stroke="none" />
    </svg>
  );
}
export function IcoShuffle() {
  return (
    <svg {...STROKE}>
      <polyline points="16 3 21 3 21 8" />
      <line x1="4" y1="20" x2="21" y2="3" />
      <polyline points="21 16 21 21 16 21" />
      <line x1="15" y1="15" x2="21" y2="21" />
      <line x1="4" y1="4" x2="9" y2="9" />
    </svg>
  );
}
export function IcoRepeat() {
  return (
    <svg {...STROKE}>
      <polyline points="17 1 21 5 17 9" />
      <path d="M3 11V9a4 4 0 0 1 4-4h14" />
      <polyline points="7 23 3 19 7 15" />
      <path d="M21 13v2a4 4 0 0 1-4 4H3" />
    </svg>
  );
}
export function IcoDownload() {
  return (
    <svg {...STROKE}>
      <path d="M12 3v12" /><polyline points="7 10 12 15 17 10" /><path d="M5 21h14" />
    </svg>
  );
}
export function IcoPlaySmall() {
  return (
    <svg {...STROKE}><path d="M8.5 5.5v13L19 12z" fill="currentColor" stroke="none" /></svg>
  );
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
