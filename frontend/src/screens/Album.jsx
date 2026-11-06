/* Экран альбома: обложка, год, длительность, треклист с номерами. */
import React, { useEffect, useState } from 'react';
import { API_BASE, coverUrl } from '../api.js';
import * as P from '../player.js';
import { haptic } from '../tg.js';
import {
  TrackRow, TrackMenu, Skeletons, fmtTime, dominantColor, gradFor, IcoNote,
} from '../ui.jsx';
import * as routes from '../routes.js';

export default function Album({ album }) {
  const [glow, setGlow] = useState(null);
  const [menu, setMenu] = useState(null);
  const tracks = album.tracks || [];

  useEffect(() => {
    let dead = false;
    dominantColor(album.cover || '').then(rgb => { if (!dead && rgb) setGlow(rgb); });
    return () => { dead = true; };
  }, [album.key]);

  const coverSrc = album.cover ? (album.cover.startsWith('http') ? album.cover : API_BASE + album.cover) : '';
  const totalDur = tracks.reduce((s, t) => s + (t.duration || 0), 0);
  const year = album.year || (tracks.find(t => t.year)?.year) || '';

  return (
    <div>
      <div className="page-head">
        <button className="page-back" onClick={() => window.history.back()}>‹ Назад</button>
      </div>
      <div className="ahero-card rise" style={{
        '--g1': glow ? `rgb(${glow[0]},${glow[1]},${glow[2]})` : '#2a2d35',
      }}>
        <div className="ahero-top">
          <span className="ahero-cover">
            {coverSrc
              ? <img src={coverSrc} alt="" />
              : <span className="cph" style={{ background: gradFor(album.title || 'a') }}>
                  <IcoNote width="1em" height="1em" style={{ width: '30%', height: '30%' }} />
                </span>}
          </span>
          <div className="ahero-meta">
            <div className="ahero-kind">Альбом{year ? ` · ${year}` : ''}</div>
            <div className="ahero-name">{album.title}</div>
            <div className="ahero-note">
              <b>{album.artist || ''}</b>
              <span>· {tracks.length} трек(ов)</span>
              {totalDur > 0 && <span>· {fmtTime(totalDur)}</span>}
            </div>
          </div>
        </div>
        <div className="ahero-cta">
          <button className="pbtn-lg" onClick={() => { haptic('medium'); tracks.length && P.play(tracks, 0); }}
                  aria-label="Слушать альбом">
            <svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22"><path d="M8.2 5.6v12.8L18.6 12z" /></svg>
          </button>
          {album.artist && (
            <button className="bigbtn" onClick={() => routes.goArtist(album.artist, album.cover)}>
              Исполнитель
            </button>
          )}
        </div>
      </div>

      <div className="tracks">
        {tracks.map((t, i) => (
          <TrackRow key={t.id + ':' + i} t={t} i={i} num={i + 1} time={t.duration}
                    onPlay={() => P.play(tracks, i)}
                    onMenu={() => setMenu({ ...t, album: album.title, year })} />
        ))}
      </div>
      {menu && <TrackMenu t={menu} onClose={() => setMenu(null)} />}
    </div>
  );
}
