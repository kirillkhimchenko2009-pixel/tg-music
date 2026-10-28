/* Экран «Музыка»: то, что уже скачано (file_id готов) — играет мгновенно. */
import React, { useEffect, useState } from 'react';
import { api } from '../api.js';
import { play, usePlayerSnap } from '../player.js';
import { haptic } from '../tg.js';
import { coverOr, Skeletons } from '../App.jsx';

export default function Library() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');
  const st = usePlayerSnap();
  const cur = st.queue[st.index];
  useEffect(() => {
    api('/api/library?limit=120').then(setData).catch(e => setErr(e.message));
  }, []);
  if (err) return <div className="err p rise">{err}</div>;
  if (!data) return <div className="p"><Skeletons n={8} /></div>;
  const t = data.tracks || [];
  if (!t.length) return (
    <div className="empty">
      <div className="big float">♪</div>
      Тут появятся треки после первого скачивания.<br />
      Загляни в «Импорт» — вставь ссылку на плейлист ЯМ.
    </div>
  );
  return (
    <div>
      <h1 className="h">Моя музыка</h1>
      <div className="list">
        {t.map((x, i) => (
          <div className={'row rise' + (cur && cur.id === x.id ? ' playing' : '')}
               key={x.id} style={{ animationDelay: Math.min(i, 12) * 30 + 'ms' }}
               onClick={() => { haptic('light'); play(t, i); }}>
            <img className="cov" src={coverOr(x)} alt="" />
            <div className="rowmeta">
              <div className="rt">{x.title}</div>
              <div className="ra">{x.artist}</div>
            </div>
            <span className={'qbadge q' + (x.quality || 'a')}>{(x.quality || 'mp3').toUpperCase()}</span>
            <span className="pbtn">▶</span>
          </div>
        ))}
      </div>
    </div>
  );
}
