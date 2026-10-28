/* Экран поиска: локальный каталог + сеть; без debounce-спама. */
import React, { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { play, usePlayerSnap } from '../player.js';
import { haptic } from '../tg.js';
import { coverOr, Skeletons } from '../App.jsx';

export default function SearchScreen() {
  const [q, setQ] = useState('');
  const [res, setRes] = useState({ local: [], web: [] });
  const [busy, setBusy] = useState(false);
  const [jobs, setJobs] = useState({});    // id -> text
  const timer = useRef(null);

  const run = (qq) => {
    if (!qq.trim()) { setRes({ local: [], web: [] }); return; }
    setBusy(true);
    api('/api/search?q=' + encodeURIComponent(qq) + '&limit=30')
      .then(setRes).catch(e => setRes({ local: [], web: [], err: e.message }))
      .finally(() => setBusy(false));
  };

  const onChange = (v) => {
    setQ(v);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => run(v), 400);
  };

  const download = async (track) => {
    haptic('medium');
    setJobs(j => ({ ...j, [track.id]: 'в очереди' }));
    try {
      const { job } = await api('/api/download?track_id=' + track.id, { method: 'POST' });
      let it = 0;
      while (it++ < 400) {
        const st = await api('/api/jobs/' + job);
        setJobs(j => ({ ...j, [track.id]: st.status === 'done' ? '✓ в чате'
          : st.status === 'error' ? 'ошибка' : '⬇ ' + Math.round((st.progress || 0) * 100) + '%' }));
        if (st.status === 'done' || st.status === 'error') break;
        await new Promise(r => setTimeout(r, 1500));
      }
    } catch (e) {
      setJobs(j => ({ ...j, [track.id]: e.message }));
    }
  };

  return (
    <div>
      <input className="searchbox" placeholder="Найди песню, исполнителя…" autoFocus
             value={q} onChange={e => onChange(e.target.value)} />
      {busy && !res.local?.length && !res.web?.length && <Skeletons n={5} />}
      {(res.local || [])?.length > 0 && <>
        <div className="cap">Из скачанного</div>
        <List tracks={res.local} jobs={jobs} onDl={download} />
      </>}
      {(res.web || [])?.length > 0 && <>
        <div className="cap">Из сети</div>
        <List tracks={res.web} jobs={jobs} onDl={download} />
      </>}
      {res.err && <div className="err p rise">{res.err}</div>}
      {!busy && q && !res.local?.length && !res.web?.length &&
        <div className="empty">Ничего не нашлось — попробуй короче: «артист трек»</div>}
    </div>
  );
}

function List({ tracks, jobs, onDl }) {
  const st = usePlayerSnap();
  const cur = st.queue[st.index];
  return (
    <div className="list">
      {tracks.map((x, i) => (
        <div className={'row rise' + (cur && cur.id === x.id ? ' playing' : '')}
             key={x.source + x.id}
             style={{ animationDelay: Math.min(i % 30, 12) * 28 + 'ms' }}>
          <img className="cov" src={coverOr(x)} alt="" onClick={() => { haptic('light'); play(tracks, i); }} />
          <div className="rowmeta" onClick={() => { haptic('light'); play(tracks, i); }}>
            <div className="rt">{x.title}</div>
            <div className="ra">{x.artist}</div>
          </div>
          <button className="dlbtn" onClick={() => onDl(x)}>{jobs[x.id] || '⬇'}</button>
        </div>
      ))}
    </div>
  );
}
