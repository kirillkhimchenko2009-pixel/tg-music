import React, { useCallback, useEffect, useMemo, useState } from 'react';
import * as P from '../player.js';
import { API_BASE, api } from '../api.js';
import { haptic } from '../tg.js';
import { FadeImg, IcoDl, IcoMic, IcoSearch, IcoX, gradFor } from '../ui.jsx';
import { cacheTracks } from '../local/repo.js';
import * as downloads from '../local/downloads.js';
import { toast } from '../motion/utils/toast.js';
import { filterPodcasts, formatPodcastDate, formatPodcastDuration, podcastTrackId } from '../podcasts.js';

const podcastTrack = (show, episode) => ({
  id: podcastTrackId(episode.id),
  title: episode.title,
  artist: show.title,
  album: show.title,
  duration: episode.duration || 0,
  cover_url: episode.cover_url || show.cover_url || null,
  audio_url: episode.audio_url,
  source: 'podcast-rss',
  ready: true,
});

function downloadUrl(episode) {
  return API_BASE + episode.audio_url + '?download=1';
}

export default function Podcasts() {
  const [shows, setShows] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [category, setCategory] = useState('Все');
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(true);
  const [savingId, setSavingId] = useState('');
  const [error, setError] = useState('');

  const loadCatalog = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 22000);
      let data;
      try { data = await api('/api/podcasts/catalog', { signal: controller.signal }); }
      finally { clearTimeout(timeout); }
      const next = Array.isArray(data.shows) ? data.shows : [];
      setShows(next);
      setSelectedId(current => next.some(show => show.id === current) ? current : next[0]?.id || '');
    } catch (e) {
      setError(e?.message || 'Не удалось загрузить RSS-каталог');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { loadCatalog(); }, [loadCatalog]);

  const categories = useMemo(() => ['Все', ...new Set(shows.map(show => show.category).filter(Boolean))], [shows]);
  const visibleShows = useMemo(() => filterPodcasts(shows, query, category), [shows, query, category]);
  const selected = visibleShows.find(show => show.id === selectedId) || visibleShows[0] || null;
  const episodes = selected?.episodes || [];

  const playEpisode = (show, episode) => {
    const list = (show.episodes || []).map(item => podcastTrack(show, item));
    const index = list.findIndex(track => track.id === podcastTrack(show, episode).id);
    if (index < 0) return;
    haptic('medium');
    P.play(list, index);
  };

  const saveEpisode = async (show, episode) => {
    if (savingId) return;
    haptic('medium');
    setSavingId(episode.id);
    const track = podcastTrack(show, episode);
    try {
      await cacheTracks([track]);
      const result = await downloads.enqueue(track, 2);
      toast(result === 'done' ? 'Выпуск уже сохранён офлайн' : 'Загрузка выпуска началась — прогресс в «Хранилище»',
        { kind: result === 'done' ? 'info' : 'success' });
    } catch (e) {
      toast(e?.message || 'Не удалось сохранить выпуск', { kind: 'error' });
    } finally {
      setSavingId('');
    }
  };

  return (
    <div className="podcasts-page">
      <header className="podcasts-heading">
        <div>
          <div className="podcasts-eyebrow"><IcoMic /> ТВОИ ИСТОРИИ В ЗВУКЕ</div>
          <h1 className="h">Подкасты</h1>
          <p className="podcasts-subtitle">Свежие выпуски из открытых RSS-лент — слушай онлайн или скачивай.</p>
        </div>
        <button type="button" className="podcasts-refresh" onClick={loadCatalog} disabled={busy}>
          <span className={busy ? 'podcasts-refresh-spin' : ''} aria-hidden="true">↻</span> Обновить
        </button>
      </header>

      {error && (
        <div className="podcasts-error" role="alert">
          <div><strong>Не удалось обновить подкасты</strong><span>{error}</span></div>
          <button className="chip" onClick={loadCatalog} disabled={busy}>Повторить</button>
        </div>
      )}

      {busy && !shows.length ? (
        <section className="podcasts-loading" aria-label="Загружаю каталог подкастов">
          <span className="podcasts-loading-pulse"><IcoMic /></span>
          <strong>Собираем свежие выпуски</strong>
          <span>Подключаемся к RSS-лентам подкастов</span>
          <div className="podcasts-skeletons"><i /><i /><i /></div>
        </section>
      ) : (
        <>
          <section className="podcasts-tools" aria-label="Поиск и темы">
            <label className="podcasts-search">
              <IcoSearch />
              <span className="sr-only">Поиск подкастов и выпусков</span>
              <input type="search" value={query} onChange={event => setQuery(event.target.value)}
                     placeholder="Найти подкаст или выпуск" />
              {query && <button type="button" className="podcasts-clear" aria-label="Очистить поиск" onClick={() => setQuery('')}><IcoX /></button>}
            </label>
            <div className="podcasts-filters" role="group" aria-label="Фильтр по теме">
              {categories.map(item => <button key={item} type="button" className={'chip' + (category === item ? ' on' : '')}
                aria-pressed={category === item} onClick={() => { haptic('soft'); setCategory(item); }}>{item}</button>)}
            </div>
          </section>

          {selected ? (
            <>
              <section className="podcasts-feature" style={{ '--show-color': selected.color || '#b4cd4b' }}>
                <div className="podcasts-feature-cover">
                  {selected.cover_url ? <FadeImg src={selected.cover_url} alt="" /> : <span style={{ background: gradFor(selected.title) }}><IcoMic /></span>}
                  <i aria-hidden="true" />
                </div>
                <div className="podcasts-feature-info">
                  <span className="podcasts-live"><i /> ОТКРЫТАЯ RSS-ЛЕНТА</span>
                  <h2>{selected.title}</h2>
                  <p>{selected.description}</p>
                  <div className="podcasts-feature-meta"><span>{selected.category}</span><span>{episodes.length} свежих выпусков</span></div>
                  {episodes[0] && <button type="button" className="bigbtn primary podcasts-feature-play" onClick={() => playEpisode(selected, episodes[0])}>
                    <span aria-hidden="true">▶</span> Слушать новый выпуск
                  </button>}
                  {selected.error && <span className="podcasts-feed-warning">Лента временно недоступна — показан последний сохранённый каталог.</span>}
                </div>
              </section>

              <section className="podcasts-layout">
                <aside className="podcasts-shows">
                  <div className="podcasts-section-head"><div><div className="cap">ВЫБИРАЙ ПО НАСТРОЕНИЮ</div><h2>Подборка</h2></div><span className="podcasts-count">{visibleShows.length}</span></div>
                  {visibleShows.map(show => (
                    <button type="button" key={show.id} className={'podcasts-show' + (show.id === selected.id ? ' active' : '')}
                      onClick={() => { haptic('soft'); setSelectedId(show.id); }} aria-current={show.id === selected.id ? 'true' : undefined}>
                      <span className="podcasts-show-art" style={{ '--show-color': show.color || '#687044' }}>
                        {show.cover_url ? <FadeImg src={show.cover_url} alt="" /> : <IcoMic />}
                      </span>
                      <span className="podcasts-show-text"><strong>{show.title}</strong><small>{show.category} · {show.episodes?.length || 0} выпусков</small></span>
                      <span className="podcasts-show-arrow" aria-hidden="true">›</span>
                    </button>
                  ))}
                  {!visibleShows.length && <div className="podcasts-empty"><strong>Ничего не найдено</strong><span>Измени запрос или тему.</span><button className="chip" onClick={() => { setQuery(''); setCategory('Все'); }}>Сбросить фильтры</button></div>}
                </aside>

                <section className="podcasts-episodes" aria-labelledby="podcasts-episodes-title">
                  <div className="podcasts-section-head"><div><div className="cap">НОВЫЕ ИЗ RSS</div><h2 id="podcasts-episodes-title">Выпуски · {selected.title}</h2></div><span className="podcasts-count">{episodes.length}</span></div>
                  {episodes.length ? <div className="podcasts-episode-list">
                    {episodes.map((episode, index) => (
                      <article className="podcasts-episode" key={episode.id}>
                        <button type="button" className="podcasts-episode-art" style={{ '--show-color': selected.color || '#687044' }}
                          aria-label={`Слушать ${episode.title}`} onClick={() => playEpisode(selected, episode)}>
                          {episode.cover_url || selected.cover_url ? <FadeImg src={episode.cover_url || selected.cover_url} alt="" /> : <IcoMic />}
                          <span>▶</span>
                        </button>
                        <div className="podcasts-episode-info">
                          <div className="podcasts-episode-meta"><span>ВЫПУСК {String(index + 1).padStart(2, '0')}</span>{episode.published && <time dateTime={episode.published}>{formatPodcastDate(episode.published)}</time>}</div>
                          <h3>{episode.title}</h3>
                          {episode.description && <p>{episode.description}</p>}
                          <div className="podcasts-episode-bottom"><span>{formatPodcastDuration(episode.duration)}{episode.size ? ` · ${Math.round(episode.size / 1024 / 1024)} МБ` : ''}</span><div className="podcasts-episode-actions"><a href={downloadUrl(episode)} className="podcasts-download" aria-label={`Скачать файл ${episode.title}`}><IcoDl /> Скачать</a>{episode.size > 0 && episode.size <= 32 * 1024 * 1024 && <button type="button" className="podcasts-offline" aria-label={`Сохранить выпуск ${episode.title} в приложении`} disabled={!!savingId} onClick={() => saveEpisode(selected, episode)}>{savingId === episode.id ? 'Добавляю…' : 'В Vibe'}</button>}</div></div>
                        </div>
                      </article>
                    ))}
                  </div> : <div className="podcasts-empty"><span className="podcasts-empty-icon"><IcoMic /></span><strong>{selected.error ? 'RSS временно недоступен' : 'Пока нет выпусков'}</strong><span>Попробуй обновить каталог чуть позже.</span></div>}
                </section>
              </section>
              <footer className="podcasts-footnote">Выпуски и аудио предоставляют авторы подкастов через открытые RSS-ленты. Загрузка сохраняет оригинальный файл из источника.</footer>
            </>
          ) : !error ? <div className="podcasts-empty"><strong>В каталог пока не удалось получить шоу</strong><button className="chip" onClick={loadCatalog}>Обновить</button></div> : null}
        </>
      )}
    </div>
  );
}
