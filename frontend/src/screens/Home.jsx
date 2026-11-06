/* Главная: приветствие + персональная волна + рельсы карточек.
   Всё на живых данных: /api/history, /api/playlists, /api/artists/top,
   /api/library. Пустые секции честно скрываются. */
import React, { useEffect, useState } from 'react';
import { api } from '../api.js';
import * as P from '../player.js';
import { haptic } from '../tg.js';
import { firstUserName } from '../tg.js';
import {
  Rail, PlayCard, ArtistCard, SectionHead, Skeletons,
  plural, groupAlbums, gradFor, IcoWave,
} from '../ui.jsx';
import * as routes from '../routes.js';

const MOODS = [
  { q: 'lofi beats', g: 'linear-gradient(140deg,#4a5468,#232a36)', ic: '🌙', t: 'Тихий вечер' },
  { q: 'драйвовый рок', g: 'linear-gradient(140deg,#6b4a3d,#2e201a)', ic: '⚡', t: 'Энергия' },
  { q: 'русский рэп новинки', g: 'linear-gradient(140deg,#4d5a3a,#222a1a)', ic: '🎤', t: 'Рэп' },
  { q: 'танцевальная электроника', g: 'linear-gradient(140deg,#3a4d6b,#1a2a3e)', ic: '✦', t: 'Танцпол' },
  { q: 'джаз кафе', g: 'linear-gradient(140deg,#6b5a3a,#2e261a)', ic: '☕', t: 'Джаз' },
  { q: 'грустные песни', g: 'linear-gradient(140deg,#4d3d5f,#221a2d)', ic: '💧', t: 'Грусть' },
  { q: 'road trip indie', g: 'linear-gradient(140deg,#3d6b5d,#1a2e2a)', ic: '🚗', t: 'Дорога' },
  { q: 'классика фортепиано', g: 'linear-gradient(140deg,#5c5c68,#26262e)', ic: '🎹', t: 'Классика' },
];

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return 'Тихой ночи';
  if (h < 12) return 'Доброе утро';
  if (h < 18) return 'Добрый день';
  return 'Добрый вечер';
}

export default function Home() {
  const [hist, setHist] = useState(null);
  const [pls, setPls] = useState([]);
  const [artists, setArtists] = useState([]);
  const [lib, setLib] = useState([]);
  const [ready, setReady] = useState(0);

  useEffect(() => {
    api('/api/history?limit=18').then(d => setHist(d.tracks || [])).catch(() => setHist([]))
      .finally(() => setReady(r => r + 1));
    api('/api/playlists').then(d => setPls(d.playlists || [])).catch(() => setPls([]))
      .finally(() => setReady(r => r + 1));
    api('/api/artists/top?limit=10').then(d => setArtists(d.artists || [])).catch(() => setArtists([]))
      .finally(() => setReady(r => r + 1));
    api('/api/library?limit=60').then(d => setLib(d.tracks || [])).catch(() => setLib([]))
      .finally(() => setReady(r => r + 1));
  }, []);

  const loaded = ready >= 4;
  const name = firstUserName() || 'друзяка';
  const likesPl = pls.find(p => /любимое/i.test(p.title || ''));
  const myPls = pls.filter(p => p.source === 'import' && p.count > 0)
    .concat(pls.filter(p => p.source !== 'import' && !/любимое/i.test(p.title || '') && p.count > 0));
  const recentAlbums = groupAlbums(lib).slice(0, 14);

  if (!loaded) return <div style={{ padding: '22px 2px' }}><Skeletons n={5} grid /></div>;

  const allEmpty = !hist?.length && !myPls.length && !artists.length && !lib.length;

  return (
    <div>
      {/* приветствие */}
      <h1 className="h">{greeting()}, {name}</h1>
      <div className="sub">Волна уже пульсирует — включай и слушай.</div>

      {/* большой персональный микс (Моя волна) */}
      <div className="mixcard rise" style={{ marginTop: 22 }}
           onClick={() => { haptic('medium'); routes.goWave(); }}>
        <span className="mix-ava"><IcoWave width="1em" height="1em" /></span>
        <span className="mix-m">
          <span className="mix-kicker">Персональный поток</span>
          <div className="mix-name">Моя волна</div>
          <div className="mix-sub">Собственный движок знает твой вкус — жанры, настроение и занятие</div>
        </span>
        <span className="mix-play">
          <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8.6 5.4v13.2L19.4 12z" /></svg>
        </span>
      </div>

      {/* Продолжить слушать */}
      {hist?.length > 0 && (
        <section>
          <SectionHead title="Продолжить слушать" />
          <Rail>
            {hist.map((t, i) => (
              <PlayCard key={'h' + t.id} title={t.title} sub={t.artist}
                        onOpen={() => { haptic('light'); P.play(hist, i); }}
                        onPlay={() => P.play(hist, i)}
                        cellDelay={Math.min(i, 8) * 40} />
            ))}
          </Rail>
        </section>
      )}

      {/* популярное сегодня: плейлисты */}
      {myPls.length > 0 && (
        <section>
          <SectionHead title="Популярное сегодня" action="Все плейлисты"
                       onAction={() => routes.goTab('collection', 'playlists')} />
          <Rail>
            {myPls.slice(0, 12).map((p, i) => (
              <PlayCard key={p.id} title={p.title.replace(/^❤\s*/, '')}
                        sub={plural(p.count, 'трек', 'трека', 'треков')}
                        onOpen={() => routes.goPlaylist(p)}
                        onPlay={() => routes.goPlaylist(p)}
                        cellDelay={i * 40} />
            ))}
          </Rail>
        </section>
      )}

      {/* новые релизы (по-честному: свежее в твоей коллекции, нарезка альбомов) */}
      {recentAlbums.length > 0 && (
        <section>
          <SectionHead title="Новые альбомы" action="Вся коллекция"
                       onAction={() => routes.goTab('collection', 'albums')} />
          <Rail>
            {recentAlbums.map((a, i) => (
              <PlayCard key={a.key} title={a.title} sub={`${a.artist}${a.year ? ' · ' + a.year : ''}`}
                        onOpen={() => routes.goAlbum(a)}
                        onPlay={() => P.play(a.tracks, 0)}
                        cellDelay={i * 40} />
            ))}
          </Rail>
        </section>
      )}

      {/* рекомендации-исполнители */}
      {artists.length > 0 && (
        <section>
          <SectionHead title="Рекомендуемые исполнители" action="Все"
                       onAction={() => routes.goTab('collection', 'artists')} />
          <Rail>
            {artists.map((a, i) => (
              <ArtistCard key={a.name} name={a.name} cover={a.cover_url}
                          sub={plural(a.count, 'трек', 'трека', 'треков')}
                          onOpen={() => routes.goArtist(a.name, a.cover_url)}
                          delay={i * 40} />
            ))}
          </Rail>
        </section>
      )}

      {/* по настроению */}
      <section>
        <SectionHead title="По настроению" />
        <div className="moodgrid">
          {MOODS.map(m => (
            <button key={m.q} className="mood" style={{ '--g': m.g }}
                    onClick={() => { haptic('medium'); routes.goSearch(m.q); }}>
              <span className="ic">{m.ic}</span>
              {m.t}
            </button>
          ))}
        </div>
      </section>

      {/* ❤ Любимое — быстрый вход */}
      {likesPl && likesPl.count > 0 && (
        <section style={{ marginTop: 26 }}>
          <SectionHead title="Любимое" />
          <button className="mixcard rise" style={{ '--g': 'linear-gradient(140deg,#3f4654,#1e2330)' }}
                  onClick={() => routes.goTab('collection', 'likes')}>
            <span className="mix-ava">
              <svg viewBox="0 0 24 24" width="38" height="38" fill="#D9ED3A">
                <path d="M12 21c-4.6-4-8.7-7-8.7-10.8C3.3 7 5.6 4.7 8.4 4.7c1.6 0 3.1.8 4.1 2 1-1.2 2.4-2 4-2 2.8 0 5.1 2.3 5.1 5.5 0 3.8-4.1 6.8-8.7 10.8z" />
              </svg>
            </span>
            <span className="mix-m">
              <span className="mix-kicker">Плейлист</span>
              <div className="mix-name">Любимое</div>
              <div className="mix-sub">{plural(likesPl.count, 'трек', 'трека', 'треков')} · всё, что ты отмечал сердцем</div>
            </span>
            <span className="mix-play">
              <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8.6 5.4v13.2L19.4 12z" /></svg>
            </span>
          </button>
        </section>
      )}

      {allEmpty && (
        <div className="empty" style={{ marginTop: 40 }}>
          <div className="big float">🎧</div>
          Здесь соберётся твоя музыка.<br />
          Импортируй плейлист по ссылке или найди треки в поиске.
        </div>
      )}
    </div>
  );
}
