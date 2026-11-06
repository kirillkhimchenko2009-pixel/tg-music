/* Экран исполнителя: геро-градиент по обложке, популярные, альбомы,
   всё собрано из результатов поиска по имени. */
import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';
import * as P from '../player.js';
import { haptic } from '../tg.js';
import {
  PlayCard, TrackRow, TrackMenu, SectionHead, Skeletons,
  fmtTime, groupAlbums, dominantColor, gradFor, IcoNote,
} from '../ui.jsx';
import * as routes from '../routes.js';

export default function Artist({ name, cover }) {
  const [rows, setRows] = useState(null);
  const [glow, setGlow] = useState(null);
  const [menu, setMenu] = useState(null);

  useEffect(() => {
    api('/api/search?q=' + encodeURIComponent(name) + '&limit=50')
      .then(d => {
        const all = [...(d.local || []), ...(d.web || [])];
        const nl = (name || '').toLowerCase();
        const own = all.filter(t => (t.artist || '').toLowerCase().includes(nl));
        const seen = new Set();
        setRows(own.filter(t => {
          const k = (t.title || '') + '|' + (t.artist || '');
          if (seen.has(k)) return false;
          seen.add(k);
          return t.title;
        }));
      })
      .catch(() => setRows([]));
  }, [name]);

  useEffect(() => {
    let dead = false;
    dominantColor(cover || '').then(rgb => { if (!dead && rgb) setGlow(rgb); });
    return () => { dead = true; };
  }, [cover]);

  const albums = useMemo(() => rows ? groupAlbums(rows).sort((a, b) => (b.year || 0) - (a.year || 0)) : [], [rows]);
  const totalDur = (rows || []).reduce((s, t) => s + (t.duration || 0), 0);

  if (!rows) return <div style={{ padding: '18px 2px' }}><Skeletons n={6} /></div>;

  const popular = rows.slice(0, 12);
  const rest = rows.slice(12);

  return (
    <div>
      <div className="ahero-card rise" style={{
        '--g1': glow ? `rgb(${glow[0]},${glow[1]},${glow[2]})` : '#2a2d35',
      }}>
        <div className="ahero-top">
          <span className="ahero-circ">
            {cover
              ? <img src={(cover.startsWith('http') ? cover : (cover.startsWith('/') ? API_BASE_URL(cover) : cover))} alt=""
                     onError={e => { e.currentTarget.style.visibility = 'hidden'; }} />
              : <span className="artinit" style={{ fontSize: 48 }}>{(name || '?').slice(0, 1).toUpperCase()}</span>}
          </span>
          <div className="ahero-meta">
            <div className="ahero-kind">Исполнитель</div>
            <div className="ahero-name">{name}</div>
            <div className="ahero-note">
              <b>{rows.length}</b><span>треков найдено</span>
              {totalDur > 0 && <span>· {fmtTime(totalDur)} в сумме</span>}
            </div>
          </div>
        </div>
        <div className="ahero-cta">
          <button className="pbtn-lg" onClick={() => { haptic('medium'); rows.length && P.play(rows, 0); }}
                  aria-label="Слушать исполнителя">
            <svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22"><path d="M8.2 5.6v12.8L18.6 12z" /></svg>
          </button>
          <button className="iconact" aria-label="Перемешать все"
                  onClick={() => { haptic('medium'); if (rows.length) P.play([...rows].sort(() => Math.random() - .5), 0); }}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="18" height="18">
              <path d="M16 3.5h4.5V8" /><path d="M4 20 20.5 3.5" /><path d="M20.5 16v4.5H16" /><path d="m15 15 5.5 5.5" /><path d="M4 4l5.5 5.5" />
            </svg>
          </button>
        </div>
      </div>

      {popular.length > 0 && (
        <section>
          <SectionHead title="Популярное" />
          <div className="tracks">
            {popular.map((t, i) => (
              <TrackRow key={t.id} t={t} i={i} num={i + 1} time={t.duration}
                        onPlay={() => P.play(popular, i)}
                        onMenu={() => setMenu(t)} />
            ))}
          </div>
        </section>
      )}

      {albums.length > 0 && (
        <section>
          <SectionHead title="Альбомы" />
          <div className="albgrid">
            {albums.slice(0, 16).map((a, i) => (
              <PlayCard key={a.key} title={a.title} sub={`${a.year || ''}`.trim()}
                        onOpen={() => routes.goAlbum(a)}
                        onPlay={() => P.play(a.tracks, 0)}
                        cellDelay={Math.min(i, 8) * 40} />
            ))}
          </div>
        </section>
      )}

      {rest.length > 0 && (
        <section>
          <SectionHead title="Ещё треки" />
          <div className="tracks">
            {rest.slice(0, 30).map((t, i) => (
              <TrackRow key={t.id + ':' + i} t={t} i={i} time={t.duration}
                        onPlay={() => P.play(rest, i)}
                        onMenu={() => setMenu(t)} />
            ))}
          </div>
        </section>
      )}

      {!rows.length && (
        <div className="empty">
          <div className="big float">🫥</div>
          Ничего не нашлось по «{name}». Возможно, треков ещё нет в открытых источниках.
        </div>
      )}

      {menu && <TrackMenu t={menu} onClose={() => setMenu(null)} />}
    </div>
  );
}

function API_BASE_URL(u) {
  return `${window.location.origin}${u}`;
}
