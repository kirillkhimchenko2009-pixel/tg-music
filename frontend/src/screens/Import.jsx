/* Импорт: вставь ссылку — плейлист соберётся в коллекции.
   ЯМ / Звук / Spotify / YouTube / SoundCloud — всё публичное, без токенов. */
import React, { useState } from 'react';
import { api, API_BASE, coverUrl } from '../api.js';
import * as P from '../player.js';
import { haptic } from '../tg.js';
import { TrackRow, Skeletons, fmtTime, IcoDl, coverOr } from '../ui.jsx';
import * as routes from '../routes.js';

const SERVICES = [
  { n: 'Яндекс.Музыка', ex: 'music.yandex.ru/playlists/…' },
  { n: 'Звук', ex: 'zvuk.com/playlist/…' },
  { n: 'SoundCloud', ex: 'soundcloud.com/…/sets/…' },
  { n: 'Spotify', ex: 'open.spotify.com/playlist/…' },
  { n: 'YouTube', ex: 'youtube.com/playlist?list=…' },
];

export default function ImportScreen() {
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
      <p className="sub" style={{ maxWidth: 560 }}>
        Вставь ссылку на плейлист — Яндекс.Музыка, Звук, SoundCloud, Spotify или YouTube.
        Публичные списки забираются без токенов и появятся в коллекции: можно слушать
        здесь и качать чанками в чат.
      </p>

      <div style={{ maxWidth: 620 }}>
        <div className="searchbar" style={{ marginTop: 16 }}>
          <input style={{ flex: 1 }} placeholder="https://music.yandex.ru/playlists/…"
                 value={url} autoFocus autoComplete="off" spellCheck="false"
                 onChange={e => setUrl(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter' && url.trim()) go(); }} />
        </div>
        <div className="btnrow">
          <button className="bigbtn primary" disabled={busy || !url.trim()} onClick={go}>
            {busy ? <><span className="spinner" style={{ width: 16, height: 16 }} /> Забираю плейлист…</> : <>Забрать плейлист</>}
          </button>
        </div>
        {err && <div className="err">{err}</div>}

        <div className="cap">Ссылки, которые понимает Vibe</div>
        <div className="tagcloud">
          {SERVICES.map(s => <span key={s.n} className="chip" style={{ cursor: 'default' }}>{s.n}</span>)}
        </div>

        {busy && <div style={{ marginTop: 18 }}><Skeletons n={4} /></div>}
        {res && (
          <div className="cardbox" style={{ marginTop: 18, animation: 'rise .4s var(--ease) both' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ width: 40, height: 40, borderRadius: '50%', display: 'grid', placeItems: 'center', background: 'var(--accent)', color: 'var(--accent-ink)' }}>✓</span>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 750, fontSize: 15 }}>«{res.title}»</div>
                <div className="pl-artist">{res.added} новых трек(ов) · в коллекции</div>
              </div>
            </div>
            <div className="btnrow">
              <button className="bigbtn primary" onClick={() => routes.goTab('collection', 'playlists')}>
                Открыть коллекцию
              </button>
              {res.id && (
                <button className="bigbtn" onClick={() => routes.goPlaylist(res)}>
                  Открыть плейлист
                </button>
              )}
            </div>
            {(res.tracks || []).slice(0, 6).map((t, i) => (
              <TrackRow key={t.id} t={t} i={i} time={t.duration}
                        heart={false}
                        onPlay={() => P.play(res.tracks, i)} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
