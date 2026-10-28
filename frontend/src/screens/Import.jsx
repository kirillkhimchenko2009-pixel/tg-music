/* Импорт плейлиста по ссылке (ЯМ / Звук / Spotify / YouTube). */
import React, { useState } from 'react';
import { api } from '../api.js';
import { play, usePlayerSnap } from '../player.js';
import { haptic } from '../tg.js';
import { coverOr, HeartBtn } from '../App.jsx';

export default function ImportScreen({ onOpenPlaylist }) {
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState('');

  const go = async () => {
    haptic('medium');
    setBusy(true); setErr(''); setRes(null);
    try {
      const j = await api('/api/import', { method: 'POST', body: JSON.stringify({ url }) });
      haptic('ok');
      setRes(j);
    } catch (e) {
      haptic('error');
      setErr(e.message);
    }
    setBusy(false);
  };

  return (
    <div>
      <h1 className="h">Импорт</h1>
      <p className="muted p">
        Вставь ссылку на плейлист — Яндекс.<wbr />Музыка, Звук, Spotify или YouTube.
        Треки появятся новым плейлистом: можно слушать здесь и качать чанками в чат.
      </p>
      <input className="searchbox" placeholder="https://music.yandex.ru/playlists/…"
             value={url} onChange={e => setUrl(e.target.value)} />
      <button className="bigbtn primary wide" disabled={busy || !url.trim()} onClick={go}>
        {busy ? <><span className="spinner spinner-dark" /> Забираю плейлист…</> : 'Импортировать'}
      </button>
      {err && <div className="err p rise">{err}</div>}
      {res && (
        <div className="cardbox l pop">
          <div className="okmsg">✓ «{res.title}» — {res.added} новых трек(ов)</div>
          <button className="bigbtn wide" onClick={() => { haptic('light'); onOpenPlaylist(); }}>
            Открыть плейлисты
          </button>
          {(res.tracks || []).slice(0, 6).map((t, i) => (
            <div className="row rise" key={t.id}
                 style={{ animationDelay: Math.min(i, 6) * 40 + 'ms' }}
                 onClick={() => { haptic('light'); play(res.tracks, res.tracks.indexOf(t)); }}>
              <img className="cov" src={coverOr(t)} alt="" />
              <div className="rowmeta">
                <div className="rt">{t.title}</div>
                <div className="ra">{t.artist}</div>
              </div>
              <HeartBtn id={t.id} />
              <span className="pbtn">▶</span>
            </div>
          ))}
          {(res.tracks || []).length > 6 && <div className="muted c">…и ещё {res.tracks.length - 6}</div>}
        </div>
      )}
    </div>
  );
}
