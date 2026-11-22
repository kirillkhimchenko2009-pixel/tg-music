/* Поиск v3: большое поле, жанры-чипы, популярные запросы,
   результат — треки (локал+сеть), альбомы, исполнители (клиентская группировка). */
import React, { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { api } from '../api.js';
import * as P from '../player.js';
import { haptic } from '../tg.js';
import {
  PlayCard, ArtistCard, TrackRow, TrackMenu, Skeletons, Rail, groupAlbums, IcoX, IcoSearch,
} from '../ui.jsx';
import * as routes from '../routes.js';
import Reveal from '../motion/components/Reveal.jsx';

import gHiphop from '../assets/genre-hiphop.jpg';
import gRock from '../assets/genre-rock.jpg';
import gPop from '../assets/genre-pop.jpg';
import gElectro from '../assets/genre-electro.jpg';
import gJazz from '../assets/genre-jazz.jpg';
import gRus from '../assets/genre-rus.jpg';
import gClassic from '../assets/genre-classic.jpg';
import gLofi from '../assets/genre-lofi.jpg';

const GENRES = [
  { q: 'русские хиты', t: 'Русские хиты', g: 'linear-gradient(140deg,#42506a,#1e2634)', ic: '🇷🇺', img: gRus },
  { q: 'hip hop', t: 'Хип-хоп', g: 'linear-gradient(140deg,#4a3f63,#241e33)', ic: '🎤', img: gHiphop },
  { q: 'рок', t: 'Рок', g: 'linear-gradient(140deg,#6b4440,#2e1d1e)', ic: '🎸', img: gRock },
  { q: 'поп', t: 'Поп', g: 'linear-gradient(140deg,#3f5d5a,#1c2c2c)', ic: '✦', img: gPop },
  { q: 'електроника', t: 'Электроника', g: 'linear-gradient(140deg,#40506e,#1c2637)', ic: '🌀', img: gElectro },
  { q: 'джаз', t: 'Джаз', g: 'linear-gradient(140deg,#6b5a3a,#2e2618)', ic: '🎷', img: gJazz },
  { q: 'classical', t: 'Классика', g: 'linear-gradient(140deg,#565c68,#25282e)', ic: '🎻', img: gClassic },
  { q: 'лоу-фай', t: 'Лоу-фай', g: 'linear-gradient(140deg,#4d5f42,#20291c)', ic: '🌙', img: gLofi },
];

const POPULAR = ['казалось-бы тренды', 'афиша хиты 2026', 'русский рэп', 'кафе лоу-фай', 'дорожный рок', 'фоновая классика'];

export default function SearchScreen({ initial = '' }) {
  const [q, setQ] = useState(initial);
  const [res, setRes] = useState({ local: [], web: [] });
  const [busy, setBusy] = useState(false);
  const [menu, setMenu] = useState(null);
  const inputRef = useRef(null);
  const timer = useRef(null);
  const requestId = useRef(0);
  const abortRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
    return () => {
      clearTimeout(timer.current);
      abortRef.current?.abort();
    };
  }, []);

  const run = qq => {
    clearTimeout(timer.current);
    abortRef.current?.abort();
    const id = ++requestId.current;
    if (!qq.trim()) {
      setRes({ local: [], web: [] });
      setBusy(false);
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    const timeout = setTimeout(() => controller.abort(), 30000);
    setBusy(true);
    api('/api/search?q=' + encodeURIComponent(qq) + '&limit=20',
        { signal: controller.signal })
      .then(data => { if (id === requestId.current) setRes(data); })
      .catch(e => {
        if (id === requestId.current) {
          const timedOut = e.name === 'AbortError' && controller.signal.aborted;
          setRes({ local: [], web: [], err: timedOut
            ? 'Поиск не ответил за 30 секунд. Попробуй ещё раз.'
            : e.message });
        }
      })
      .finally(() => {
        clearTimeout(timeout);
        if (id === requestId.current) setBusy(false);
      });
  };

  const onChange = v => {
    setQ(v);
    clearTimeout(timer.current);
    if (!v.trim()) {
      abortRef.current?.abort();
      requestId.current += 1;
      setRes({ local: [], web: [] });
      setBusy(false);
      return;
    }
    /* busy ставим сразу, не ждём дебаунс: старые результаты гаснут и
       перестают нажиматься, как только человек изменил запрос */
    setBusy(true);
    timer.current = setTimeout(() => run(v), 420);
  };

  const localResults = res.local || [];
  const webResults = res.web || [];
  let visibleLocal = localResults.slice(0, 10);
  let visibleWeb = webResults.slice(0, 10);
  let remaining = 20 - visibleLocal.length - visibleWeb.length;
  if (remaining > 0) {
    const localMore = localResults.slice(visibleLocal.length, visibleLocal.length + remaining);
    visibleLocal = [...visibleLocal, ...localMore];
    remaining -= localMore.length;
  }
  if (remaining > 0) {
    visibleWeb = [...visibleWeb, ...webResults.slice(visibleWeb.length, visibleWeb.length + remaining)];
  }
  /* один список треков: скачанные идут первыми, дубли по id убираем */
  const allTracks = [];
  const seenTrackIds = new Set();
  for (const t of [...visibleLocal, ...visibleWeb]) {
    const k = String(t.id);
    if (seenTrackIds.has(k)) continue;
    seenTrackIds.add(k);
    allTracks.push(t);
  }
  const albums = groupAlbums(allTracks).slice(0, 12);
  const seen = new Set();
  const artistNames = [];
  for (const t of allTracks) {
    const a = (t.artist || '').trim();
    if (a) {
      const k = a.toLowerCase();
      if (!seen.has(k)) { seen.add(k); artistNames.push(a); }
    }
  }

  const clear = () => {
    clearTimeout(timer.current);
    abortRef.current?.abort();
    requestId.current += 1;
    setBusy(false);
    setQ('');
    setRes({ local: [], web: [] });
    inputRef.current?.focus();
  };

  return (
    <div>
      <div className="searchbar">
        <IcoSearch width="1em" height="1em" />
        <input ref={inputRef} placeholder="Найди песню, исполнителя, альбом…"
               value={q} autoFocus autoComplete="off" spellCheck="false"
               onChange={e => onChange(e.target.value)}
               onKeyDown={e => { if (e.key === 'Enter') { clearTimeout(timer.current); run(q); inputRef.current?.blur(); } }} />
        {busy && <span className="minispinner search-spin" role="status" aria-label="Идёт поиск" />}
        <AnimatePresence initial={false}>
          {q && (
            <motion.button
              className="searchclr"
              aria-label="Очистить"
              initial={{ opacity: 0, scale: .7, rotate: -8 }}
              animate={{ opacity: 1, scale: 1, rotate: 0 }}
              exit={{ opacity: 0, scale: .7, rotate: 8 }}
              transition={{ duration: .16, ease: [.16, 1, .3, 1] }}
              onClick={clear}
            >
              <IcoX width="1em" height="1em" />
            </motion.button>
          )}
        </AnimatePresence>
      </div>

      {/* пустой запрос: жанры + популярное */}
      {!q && (
        <>
          <Reveal>
            <div className="cap" style={{ marginTop: 20 }}>Жанры</div>
            <div className="moodgrid">
              {GENRES.map(g => (
                <button key={g.q} className="mood" style={{ '--g': g.g, '--img': `url(${g.img})` }}
                        onClick={() => { haptic('medium'); setQ(g.q); run(g.q); }}>
                  <span className="ic">{g.ic}</span>{g.t}
                </button>
              ))}
            </div>
          </Reveal>
          <Reveal>
            <div className="cap">Популярные запросы</div>
            <div className="tagcloud">
              {POPULAR.map(p => (
                <button key={p} className="chip" onClick={() => { haptic('medium'); setQ(p); run(p); }}>{p}</button>
              ))}
            </div>
          </Reveal>
        </>
      )}

      {/* результаты: треки первыми — до них не нужно прокручивать альбомы */}
      <AnimatePresence mode="wait" initial={false}>
        {q && busy && !allTracks.length && !albums.length && (
          <motion.div key="search-busy" style={{ marginTop: 20 }}
                      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Skeletons n={5} />
          </motion.div>
        )}

        {q && (
          <motion.div
            key={'search-results'}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: .22, ease: [.16, 1, .3, 1] }}
            className={busy ? 'search-stale' : undefined}
            aria-busy={busy || undefined}
          >
            {allTracks.length > 0 && (
              <Reveal>
                <div className="cap">Треки</div>
                <div className="tracks">
                  <AnimatePresence initial={false}>
                    {allTracks.map((t, i) => (
                      <TrackRow key={'t' + t.id} t={t} i={i} time={t.duration}
                                onPlay={() => P.play(allTracks, i)}
                                onMenu={() => setMenu(t)} />
                    ))}
                  </AnimatePresence>
                </div>
              </Reveal>
            )}
            {albums.length > 0 && (
              <Reveal>
                <div className="cap">Альбомы</div>
                <Rail>
                  {albums.map((a, i) => (
                    <PlayCard key={a.key} title={a.title}
                              sub={`${a.artist}${a.year ? ' · ' + a.year : ''}`}
                              onOpen={() => routes.goAlbum(a)}
                              onPlay={() => P.play(a.tracks, 0)}
                              cellDelay={Math.min(i, 6) * 40} />
                  ))}
                </Rail>
              </Reveal>
            )}
            {artistNames.slice(0, 8).length > 0 && (
              <Reveal>
                <div className="cap">Исполнители</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px 12px' }}>
                  {artistNames.slice(0, 8).map((name, i) => (
                    <ArtistCard key={name} name={name}
                                onOpen={() => routes.goArtist(name)}
                                delay={Math.min(i, 6) * 30} />
                  ))}
                </div>
              </Reveal>
            )}
            {res.err && (
              <div className="err search-error" role="alert">
                <span>{res.err}</span>
                <button className="bigbtn" onClick={() => run(q)}>Повторить поиск</button>
              </div>
            )}
            {!busy && !allTracks.length && !albums.length && !res.err && (
              <div className="empty">
                <div className="big float">🔎</div>
                Ничего не нашлось. Попробуй короче: «артист — трек» или просто «название песни».
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      <TrackMenu t={menu} open={!!menu} onClose={() => setMenu(null)} />
    </div>
  );
}
