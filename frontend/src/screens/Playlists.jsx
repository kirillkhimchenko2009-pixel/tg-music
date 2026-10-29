/* Плейлисты: список → деталь с треками, «слушать» и «скачать всё». */
import React, { useEffect, useState } from 'react';
import { api } from '../api.js';
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

export default function Playlists() {
  const [items, setItems] = useState(null);
  const [open, setOpen] = useState(null);   // {id,title} текущего
  const [err, setErr] = useState('');

  const reload = () => api('/api/playlists').then(d => setItems(d.playlists || [])).catch(e => setErr(e.message));
  useEffect(() => { reload(); }, []);
  if (err) return <div className="err p rise">{err}</div>;
  if (open) return <Detail pl={open} back={() => setOpen(null)} />;
  if (!items) return <div className="p"><Skeletons n={4} grid /></div>;
  return (
    <div>
      <h1 className="h">Плейлисты</h1>
      {!items.length && <div className="empty">Пусто. Импортируй ссылку из Яндекс.Музыки во вкладке «Импорт».</div>}
      <div className="plgrid">
        {items.map((p, i) => (
          <div className="plcard rise" key={p.id}
               style={{ animationDelay: Math.min(i, 8) * 40 + 'ms' }}
               onClick={() => { haptic('light'); setOpen(p); }}>
            <img className="plcov" src={p.cover_url ? coverOr(p) : ''} alt=""
                 style={{ background: p.title.includes('❤')
                   ? 'linear-gradient(135deg,#FF6A8B,#8C2F4E)'
                   : PL_GRADS[p.id % PL_GRADS.length] }} />
            <div className="plt">{p.title}</div>
            <div className="ra">{p.count} трек(ов)</div>
          </div>
        ))}
      </div>
    </div>
  );
}

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

  return (
    <div>
      <button className="back" onClick={back}>‹ Плейлисты</button>
      <div className="dhead">
        <div className="plt big">{pl.title}</div>
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
