/* Коллекция как в Яндекс.Музыке: «Любимое», любимые исполнители, мозаичные
   обложки плейлистов, вкладки «Вы собрали / Вам понравилось», создание плейлиста.
   Анимации: stagger-rise, spring-нажатия, мозаика с разворотом, шторка создания. */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api, API_BASE } from '../api.js';
import { play, usePlayerSnap } from '../player.js';
import { haptic } from '../tg.js';
import { coverOr, HeartBtn, IcoDownload, IcoPlaySmall, Skeletons } from '../App.jsx';

/* Пастельные обложки-фолбэки по кругу — как художественные сетки YM */
const PL_GRADS = [
  'linear-gradient(135deg,#4A3F63,#2A2438)',
  'linear-gradient(135deg,#5C4A2F,#3A2E1C)',
  'linear-gradient(135deg,#2F4A44,#1C2C2A)',
  'linear-gradient(135deg,#4A2F35,#2A1A1E)',
  'linear-gradient(135deg,#33405A,#1E2635)',
  'linear-gradient(135deg,#4A4A2F,#2A2A1C)',
];

const HEART = ('M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3'
  + 'c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5'
  + 'c0 3.78-3.4 6.86-8.55 11.54L12 21.35z');

const cimg = u => (!u ? '' : (u.startsWith('http') ? u : API_BASE + u));

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  const w = (m10 === 1 && m100 !== 11) ? one
    : (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) ? few : many;
  return `${n} ${w}`;
}

/* Доминирующий цвет обложки — «у вашей музыки есть цвет».
   Крошечный canvas-квантователь (аналог ColorThief, без зависимостей):
   downsample → бакеты 32³ → частота × насыщенность. CORS-фолбэк — тёплый жёлтый. */
function dominantColor(src) {
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
          if (lum < 0.10 || lum > 0.94) continue;      // чёрное/белое цветом не считаем
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

export default function Playlists({ onImport }) {
  const [items, setItems] = useState(null);
  const [artists, setArtists] = useState([]);
  const [open, setOpen] = useState(null);      // плейлист для детального экрана
  const [tab, setTab] = useState('mine');      // mine | liked
  const [creating, setCreating] = useState(false);
  const [accent, setAccent] = useState(null);
  const [err, setErr] = useState('');

  const reload = () => api('/api/playlists')
    .then(d => setItems(d.playlists || []))
    .catch(e => setErr(e.message));
  useEffect(() => {
    reload();
    api('/api/artists/top?limit=8')
      .then(d => setArtists(d.artists || []))
      .catch(() => setArtists([]));
  }, []);

  const likes = items && items[0];             // «❤ Любимое» всегда первым
  const mine = useMemo(() => (items || []).slice(1)
    .filter(p => p.source !== 'import'), [items]);
  const liked = useMemo(() => (items || []).slice(1)
    .filter(p => p.source === 'import'), [items]);

  /* цвет настроения: обложка первого любимого трека → свечение шапки */
  const firstCover = likes && likes.covers && likes.covers[0];
  useEffect(() => {
    let dead = false;
    dominantColor(cimg(firstCover)).then(rgb => {
      if (!dead) setAccent(rgb ? `rgb(${rgb.join(',')})` : null);
    });
    return () => { dead = true; };
  }, [firstCover]);

  if (err) return <div className="err p rise">{err}</div>;
  if (open) return <Detail pl={open} back={() => setOpen(null)} />;
  if (!items) return <div className="p"><Skeletons n={4} grid /></div>;

  const list = tab === 'mine' ? mine : liked;
  const glow = accent || 'rgb(255,219,77)';

  return (
    <div className="coll" style={{ '--coll-glow': glow }}>
      <div className="coll-glow" />
      <header className="coll-head">
        <h1>Коллекция</h1>
        <div className="coll-sub">У вашей музыки есть цвет</div>
      </header>

      {/* ❤ Любимое */}
      {likes && (
        <button className="likesrow rise"
                onClick={() => { haptic('light'); setOpen(likes); }}>
          <span className="likescov">
            <svg viewBox="0 0 24 24"><path d={HEART} /></svg>
            <i className="likesshine" />
          </span>
          <span className="likesmeta">
            <span className="likest">Любимое</span>
            <span className="likesn">{plural(likes.count, 'трек', 'трека', 'треков')}</span>
          </span>
          <span className="chevr"><IcoChevron /></span>
        </button>
      )}

      {/* Любимые исполнители */}
      {artists.length > 0 && (
        <section className="collsec">
          <div className="collh2">
            <h2>Любимые исполнители</h2>
          </div>
          <div className="artrow">
            {artists.map((a, i) => (
              <div className="artcard" key={a.name}
                   style={{ animationDelay: 60 + i * 45 + 'ms' }}>
                <span className={'artava' + (i === 0 ? ' top1' : '')}>
                  {a.cover_url
                    ? <img src={cimg(a.cover_url)} alt="" loading="lazy" />
                    : <span className="artinit">{(a.name || '?').slice(0, 1).toUpperCase()}</span>}
                  <span className="artrank">
                    {i === 0 && <em>♛</em>}{i + 1}
                  </span>
                </span>
                <span className="artname">{a.name}</span>
                <span className="artn">{plural(a.count, 'трек', 'трека', 'треков')}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Мои плейлисты */}
      <section className="collsec">
        <div className="collh2"><h2>Мои плейлисты</h2></div>
        <div className="seg" data-pos={tab}>
          <span className="seg-ind" />
          <button className={'segbtn' + (tab === 'mine' ? ' on' : '')}
                  onClick={() => { haptic('soft'); setTab('mine'); }}>
            Вы собрали
          </button>
          <button className={'segbtn' + (tab === 'liked' ? ' on' : '')}
                  onClick={() => { haptic('soft'); setTab('liked'); }}>
            Вам понравилось
          </button>
        </div>

        {list.length === 0 && tab === 'liked' ? (
          <div className="collempty rise">
            <div className="ceic">⇥</div>
            <div className="cet">Здесь появятся плейлисты из Яндекс.Музыки</div>
            <div className="ced">Импортируй ссылку — и она окажется в этой вкладке.</div>
            <button className="bigbtn primary" onClick={() => onImport && onImport()}>
              Импортировать
            </button>
          </div>
        ) : (
          <div className="plgrid plgridx">
            {tab === 'mine' && (
              <button className="plcardx rise" style={{ animationDelay: '0ms' }}
                      onClick={() => { haptic('medium'); setCreating(true); }}>
                <span className="plcovx newcov"><span className="plus">+</span></span>
                <span className="plname">Новый плейлист</span>
                <span className="pln">Создать</span>
              </button>
            )}
            {list.map((p, i) => (
              <PlCard key={p.id} p={p} i={i + 1}
                      onOpen={() => { haptic('light'); setOpen(p); }} />
            ))}
          </div>
        )}
      </section>

      {creating && (
        <CreateSheet
          onClose={() => setCreating(false)}
          onCreated={() => { setCreating(false); reload(); }} />
      )}
    </div>
  );
}

/* ---------------- карточка плейлиста с мозаикой ---------------- */
function PlCard({ p, i, onOpen }) {
  const covers = (p.covers || []).filter(Boolean).slice(0, 4);
  const title = (p.title || '').replace(/^❤\s*/, '');
  return (
    <button className="plcardx rise" style={{ animationDelay: Math.min(i, 8) * 45 + 'ms' }}
            onClick={onOpen}>
      <span className="plcovx">
        {covers.length >= 4 ? (
          <span className="mosaic">
            {covers.map((c, k) => <img key={k} src={cimg(c)} alt="" loading="lazy" />)}
          </span>
        ) : covers.length >= 1 ? (
          <img className="plsingle" src={cimg(covers[0])} alt="" loading="lazy" />
        ) : (
          <span className="plfill" style={{ background: PL_GRADS[p.id % PL_GRADS.length] }}>
            <svg viewBox="0 0 24 24" className="plnote">
              <path d="M9 18V5l12-2v13" fill="none" stroke="currentColor" strokeWidth="1.8"
                    strokeLinecap="round" strokeLinejoin="round" />
              <circle cx="6" cy="18" r="3" fill="currentColor" />
              <circle cx="18" cy="16" r="3" fill="currentColor" />
            </svg>
          </span>
        )}
        <i className="plshine" />
      </span>
      <span className="plname">{title}</span>
      <span className="pln">{plural(p.count, 'трек', 'трека', 'треков')}</span>
    </button>
  );
}

/* ---------------- шторка создания плейлиста ---------------- */
function CreateSheet({ onClose, onCreated }) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const ref = useRef(null);
  useEffect(() => { setTimeout(() => ref.current && ref.current.focus(), 120); }, []);

  const submit = async () => {
    const title = name.trim();
    if (!title || busy) return;
    setBusy(true); setErr('');
    try {
      await api('/api/playlists', { method: 'POST', body: JSON.stringify({ title }) });
      haptic('ok');
      onCreated();
    } catch (e) {
      setErr(e.message || 'Не получилось');
      setBusy(false);
    }
  };

  return createPortal(
    <div className="shovl" onClick={onClose}>
      <div className="sheet" onClick={e => e.stopPropagation()}>
        <span className="shgrab" />
        <div className="sht">Новый плейлист</div>
        <input ref={ref} className="shinput" placeholder="Название плейлиста"
               value={name} maxLength={120}
               onChange={e => setName(e.target.value)}
               onKeyDown={e => { if (e.key === 'Enter') submit(); }} />
        {err && <div className="err">{err}</div>}
        <div className="btnrow">
          <button className="bigbtn" onClick={onClose}>Отмена</button>
          <button className="bigbtn primary" disabled={!name.trim() || busy} onClick={submit}>
            {busy ? <span className="minispinner" /> : 'Создать'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function IcoChevron() {
  return (
    <svg viewBox="0 0 24 24" className="ico" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="9 6 15 12 9 18" />
    </svg>
  );
}

/* ---------------- детальный экран плейлиста ---------------- */
function Detail({ pl, back }) {
  const [tracks, setTracks] = useState(null);
  const [prog, setProg] = useState(null);   // {done,total}
  const [err, setErr] = useState('');
  const st = usePlayerSnap();
  const cur = st.queue[st.index];
  useEffect(() => {
    api(`/api/playlists/${pl.id}/tracks`).then(d => setTracks(d.tracks || [])).catch(e => setErr(e.message));
  }, [pl.id]);

  const downloadAll = async () => {
    if (!tracks?.length || prog) return;
    haptic('medium');
    let done = 0;
    setProg({ done, total: tracks.length });
    for (const t of tracks) {
      if (t.ready) { done += 1; setProg({ done, total: tracks.length }); continue; }
      try {
        const { job } = await api('/api/download?track_id=' + t.id, { method: 'POST' });
        let it = 0;
        while (it++ < 400) {
          const st = await api('/api/jobs/' + job);
          if (st.status === 'done' || st.status === 'error') break;
          await new Promise(r => setTimeout(r, 1500));
        }
      } catch (e) { /* идём дальше по списку */ }
      done += 1;
      setProg({ done, total: tracks.length });
    }
    haptic('ok');
    setProg(null);
  };

  const covers = (pl.covers || []).filter(Boolean).slice(0, 4);
  return (
    <div className="pldet">
      <button className="back" onClick={back}>‹ Плейлисты</button>
      <div className="dhead">
        <div className="dhero">
          <span className="dherocov">
            {covers.length >= 4 ? (
              <span className="mosaic">
                {covers.map((c, k) => <img key={k} src={cimg(c)} alt="" />)}
              </span>
            ) : covers.length >= 1 ? (
              <img className="plsingle" src={cimg(covers[0])} alt="" />
            ) : <span className="plfill" style={{ background: PL_GRADS[pl.id % PL_GRADS.length] }} />}
          </span>
          <div className="dherometa">
            <div className="ra">Плейлист</div>
            <div className="plt big">{(pl.title || '').replace(/^❤\s*/, '')}</div>
            <div className="ra">{plural(pl.count, 'трек', 'трека', 'треков')}</div>
          </div>
        </div>
        <div className="btnrow">
          <button className="bigbtn primary" onClick={() => { haptic('medium'); tracks && play(tracks, 0); }}>
            <IcoPlaySmall /> Слушать
          </button>
          <button className="bigbtn" disabled={!!prog} onClick={downloadAll}>
            {prog ? <>{prog.done}/{prog.total}<span className="minispinner" /></> : <><IcoDownload /> Скачать всё</>}
          </button>
        </div>
        {err && <div className="err">{err}</div>}
      </div>
      {!tracks && <Skeletons n={6} />}
      {tracks && tracks.length === 0 && (
        <div className="empty">
          <div className="big float">♪</div>
          Пока пусто. Добавь треки из поиска или нажимай ♥ на треках —
          они сложатся в этот плейлист.
        </div>
      )}
      <div className="list">
        {(tracks || []).map((x, i) => (
          <div className={'row rise' + (cur && cur.id === x.id ? ' playing' : '')}
               key={x.id} style={{ animationDelay: Math.min(i, 14) * 26 + 'ms' }}
               onClick={() => { haptic('light'); play(tracks, i); }}>
            <span className="num">{i + 1}</span>
            <img className="cov" src={coverOr(x)} alt="" />
            <div className="rowmeta">
              <div className="rt">{x.title}</div>
              <div className="ra">{x.artist} · {x.album || ''}</div>
            </div>
            <HeartBtn id={x.id} />
            {cur && cur.id === x.id && st.playing && <span className="eq eq-sm"><i /><i /><i /></span>}
            {x.ready && <span className="qbadge">✓</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
