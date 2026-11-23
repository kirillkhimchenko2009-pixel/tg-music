import React, { useCallback, useEffect, useRef, useState } from 'react';
import * as P from '../player.js';
import { API_BASE, api } from '../api.js';
import { haptic } from '../tg.js';
import { IcoBook, IcoDl, IcoSearch, IcoX } from '../ui.jsx';
import { cacheTracks } from '../local/repo.js';
import * as downloads from '../local/downloads.js';
import { toast } from '../motion/utils/toast.js';
import { audiobookTrackId, formatBookDuration } from '../audiobooks.js';

const MODERN_SOURCES = [
  { id: 'akniga-classic', name: 'Аудиокнига', note: 'Классика · русская и мировая', url: 'https://akniga.org/section/classic/', mark: 'А' },
  { id: 'litmarket-free', name: 'Литмаркет', note: 'Бесплатные аудиокниги', url: 'https://litmarket.ru/books/audiobooks/free', mark: 'Л' },
  { id: 'litres', name: 'ЛитРес', note: 'Новинки и бестселлеры', url: 'https://www.litres.ru/audiobooks/', mark: 'ЛР' },
  { id: 'mybook', name: 'MyBook', note: 'Современная литература и классика', url: 'https://mybook.ru/catalog/audiobooks/', mark: 'М' },
];
const LANGUAGES = [
  { id: 'rus', label: 'Русский' },
  { id: 'eng', label: 'English' },
  { id: 'all', label: 'Все языки' },
];
const PAGE_SIZE = 30;

function bookCover(book) {
  return book.cover_url || '';
}

function chapterTrack(book, chapter) {
  return {
    id: chapterTrackId(book, chapter),
    title: chapter.title,
    artist: book.author,
    album: book.title,
    duration: chapter.duration || 0,
    cover_url: book.cover_large || book.cover_url || null,
    audio_url: chapter.audio_url,
    source: 'LibriVox public domain',
    ready: true,
    skipLocalLookup: true,
  };
}

/* безопасный числовой ID главы (для сравнения с текущим треком плеера) */
function chapterTrackId(book, chapter) {
  try { return audiobookTrackId(book.id, chapter.id); } catch { return null; }
}

function BookCover({ book, className = '' }) {
  return <span className={'books-cover ' + className}>
    {bookCover(book) ? <img src={bookCover(book)} alt="" loading="lazy" onError={event => { event.currentTarget.style.display = 'none'; }} /> : null}
    <i aria-hidden="true">{book.title?.slice(0, 1) || 'V'}</i>
  </span>;
}

export default function Audiobooks() {
  const pst = P.usePlayerSnap();          // живое состояние плеера (идёт/пауза/текущая глава)
  const [books, setBooks] = useState([]);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [bookBusy, setBookBusy] = useState(false);
  const [savingId, setSavingId] = useState('');
  const [language, setLanguage] = useState('rus');
  const [totalCount, setTotalCount] = useState(0);
  const offsetRef = useRef(0);
  const hasMoreRef = useRef(false);
  const busyMoreRef = useRef(false);
  const requestRef = useRef(null);
  const bookRequestRef = useRef(0);
  const bookAbortRef = useRef(null);
  const searchTimer = useRef(null);
  const generationRef = useRef(0);

  const fetchCatalog = useCallback(async ({ search = '', append = false, generation = generationRef.current } = {}) => {
    if (append && (!hasMoreRef.current || busyMoreRef.current)) return;
    if (append) { busyMoreRef.current = true; setLoadingMore(true); }
    else { setLoading(true); setError(''); }
    const controller = new AbortController();
    requestRef.current = controller;
    const timeout = setTimeout(() => controller.abort(), 25000);
    try {
      const offset = append ? offsetRef.current : 0;
      const data = await api(`/api/audiobooks/catalog?limit=${PAGE_SIZE}&offset=${offset}&lang=${language}${search ? `&q=${encodeURIComponent(search)}` : ''}`, { signal: controller.signal });
      if (generation !== generationRef.current) return;
      const rows = Array.isArray(data.books) ? data.books : [];
      setBooks(current => append ? [...current, ...rows.filter(book => !current.some(item => item.id === book.id))] : rows);
      offsetRef.current = offset + rows.length;
      hasMoreRef.current = !!data.has_more;
      if (!search && Number.isFinite(Number(data.total))) setTotalCount(Number(data.total));
      setError('');
    } catch (cause) {
      if (generation === generationRef.current && cause?.name !== 'AbortError') setError(cause?.message || 'Каталог книг временно недоступен');
    } finally {
      clearTimeout(timeout);
      if (generation === generationRef.current) {
        setLoading(false);
        setLoadingMore(false);
        busyMoreRef.current = false;
      }
    }
  }, [language]);

  useEffect(() => {
    const generation = ++generationRef.current;
    offsetRef.current = 0;
    hasMoreRef.current = false;
    setBooks([]);
    setTotalCount(0);
    setError('');
    fetchCatalog({ generation });
    return () => {
      requestRef.current?.abort();
      bookAbortRef.current?.abort();
      clearTimeout(searchTimer.current);
    };
  }, [fetchCatalog]);

  const onLanguage = value => {
    if (value === language) return;
    clearTimeout(searchTimer.current);
    requestRef.current?.abort();
    setQuery('');
    setLanguage(value);
  };

  const onSearch = value => {
    setQuery(value);
    clearTimeout(searchTimer.current);
    requestRef.current?.abort();
    busyMoreRef.current = false;
    setLoadingMore(false);
    const generation = ++generationRef.current;
    if (!value.trim()) { fetchCatalog({ generation }); return; }
    if (value.trim().length < 2) { setBooks([]); setLoading(false); return; }
    setLoading(true);
    searchTimer.current = setTimeout(() => fetchCatalog({ search: value.trim(), generation }), 420);
  };

  const visibleBooks = books;
  const featured = visibleBooks[0] || null;

  /* что сейчас играет: id текущего трека; bookCurrentId — если это глава этой книги */
  const curTrackId = P.current()?.id ?? null;
  const bookCurrentId = selected?.chapters?.some(c => chapterTrackId(selected, c) === curTrackId)
    ? curTrackId : null;

  const closeBook = () => {
    bookRequestRef.current += 1;
    bookAbortRef.current?.abort();
    bookAbortRef.current = null;
    setSelected(null);
    setBookBusy(false);
  };

  const loadBook = async book => {
    if (selected?.id === book.id && selected.chapters) { closeBook(); return; }
    bookAbortRef.current?.abort();
    const requestId = ++bookRequestRef.current;
    const controller = new AbortController();
    bookAbortRef.current = controller;
    setSelected(book);
    setBookBusy(true);
    try {
      const detail = await api(`/api/audiobooks/book/${book.id}`, { signal: controller.signal });
      if (requestId !== bookRequestRef.current) return;
      if (detail?.id === book.id) setSelected(detail);
      if (!detail?.chapters?.length) toast('У этой записи пока нет доступных глав', { kind: 'info' });
    } catch (cause) {
      if (requestId === bookRequestRef.current && cause?.name !== 'AbortError') {
        toast(cause?.message || 'Не удалось загрузить главы', { kind: 'error' });
      }
    } finally {
      if (requestId === bookRequestRef.current) {
        bookAbortRef.current = null;
        setBookBusy(false);
      }
    }
  };

  const playBook = (book, chapters, index = 0) => {
    const tracks = chapters.map(chapter => chapterTrack(book, chapter));
    if (!tracks.length) return;
    haptic('medium');
    const cur = P.current();
    if (cur && tracks[index]?.id === cur.id) { P.toggle(); return; }   // та же глава → пауза/продолжить
    // Start audio before any IndexedDB await; mobile WebViews can drop the
    // user's playback gesture if local storage is checked first.
    P.play(tracks, index);
    cacheTracks(tracks).catch(() => {});
    toast(`▶ Играет: ${tracks[index]?.title || book.title}`, { kind: 'success' });
  };

  const saveChapter = async (book, chapter) => {
    const track = chapterTrack(book, chapter);
    setSavingId(chapter.id);
    try {
      await cacheTracks([track]);
      const result = await downloads.enqueue(track, 2);
      toast(result === 'done' ? 'Глава уже сохранена офлайн' : 'Загрузка главы началась — прогресс в «Хранилище»',
        { kind: result === 'done' ? 'info' : 'success' });
    } catch (cause) { toast(cause?.message || 'Не удалось сохранить главу', { kind: 'error' }); }
    finally { setSavingId(''); }
  };

  return <main className="books-page">
    <header className="books-header">
      <div><span className="books-overline"><IcoBook /> VIBE · БИБЛИОТЕКА</span><h1>Аудиокниги</h1>
        <p>Классика в голосах LibriVox — слушай свободно, по главам и без подписки.</p></div>
      <span className="books-library-count"><strong>{totalCount || '…'}</strong><small>публичных записей</small></span>
    </header>

    <section className="books-hero">
      <div className="books-hero-copy"><span>КОЛЛЕКЦИЯ ОБЩЕСТВЕННОГО ДОСТОЯНИЯ</span>
        <h2>Большие истории.<br /><em>Свободный доступ.</em></h2>
        <p>Романы, детективы, фантастика и поэзия из мировой классики. Главы можно слушать онлайн или сохранить на устройство.</p>
        {featured && <button className="books-hero-cta" onClick={() => loadBook(featured)}><span>Исследовать библиотеку</span><b>↓</b></button>}
      </div>
      <div className="books-hero-stack" aria-hidden="true"><i /><i /><i /><span>V</span></div>
      <div className="books-hero-caption">READ · LISTEN · REPEAT</div>
    </section>

    <div className="books-search-row">
      <label className="books-search"><IcoSearch /><span className="sr-only">Поиск книг и авторов</span>
        <input value={query} onChange={event => onSearch(event.target.value)} placeholder="Название книги или автор" type="search" />
        {query && <button type="button" aria-label="Очистить поиск" onClick={() => onSearch('')}><IcoX /></button>}
      </label>
      <label className="books-language-badge">
        <span className="sr-only">Язык книг</span>
        <select value={language} onChange={event => onLanguage(event.target.value)} aria-label="Язык книг">
          {LANGUAGES.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
      </label>
    </div>

    <div className="books-section-heading"><div><span>ЛЕНТА LIBRIVOX</span><h2>{query ? 'Результаты поиска' : 'Популярные книги'}</h2></div>
      <span>{query ? `${visibleBooks.length} найдено` : 'По популярности'}</span></div>

    {error && <div className="books-error" role="alert"><span>{error}</span><button onClick={() => fetchCatalog({ search: query.trim() })}>Повторить</button></div>}
    {loading && !books.length ? <div className="books-loading"><span className="books-loading-icon"><IcoBook /></span><strong>Открываем библиотеку</strong><small>Ищем книги и записи в каталоге LibriVox…</small><div><i /><i /><i /><i /></div></div> : null}
    {!loading && !visibleBooks.length && !error ? <div className="books-empty"><IcoBook /><strong>{query ? 'Ничего не найдено' : 'Пока нет книг'}</strong><span>Попробуй изменить запрос или открыть один из каталогов ниже.</span></div> : null}

    <section className="books-grid" aria-label="Каталог аудиокниг">
      {visibleBooks.map((book, index) => <article key={book.id} className={'book-card' + (featured?.id === book.id ? ' book-card-featured' : '')} style={{ '--book-accent': ['#c99a55','#a87c55','#718372','#8f7890','#627f8b'][index % 5] }}>
        <button type="button" className="book-card-hit" aria-label={`Открыть ${book.title}`} onClick={() => loadBook(book)}>
          <BookCover book={book} />
          <span className="book-card-overlay"><span>ОТКРЫТЬ КНИГУ</span><b>↗</b></span>
        </button>
        <div className="book-card-meta"><span>{book.language || 'Язык не указан'}</span><span>{book.duration ? formatBookDuration(book.duration) : 'LibriVox'}</span></div>
        <h3>{book.title}</h3><p>{book.author}</p>
        <button type="button" className="book-card-play" onClick={() => loadBook(book)}><span>＋</span> Главы и прослушивание</button>
      </article>)}
    </section>
    {loadingMore && <div className="books-more">Загружаем следующую страницу…</div>}
    {!query && hasMoreRef.current && !loadingMore && books.length > 0 && <button className="books-load-more" onClick={() => fetchCatalog({ append: true, generation: generationRef.current })}>Показать ещё книги <span>↓</span></button>}
    <p className="books-source-note">Каталог и аудиозаписи предоставляет <a href="https://librivox.org" target="_blank" rel="noreferrer">LibriVox</a>; аудиофайлы хранятся на Internet Archive. Обложки предоставляет <a href="https://openlibrary.org" target="_blank" rel="noreferrer">Open Library</a>. Книги отсортированы по числу скачиваний; язык можно переключить — русские записи, английские или все. Состав и доступность зависят от источника.</p>

    <section className="books-modern">
      <div className="books-modern-title"><span>НОВИНКИ И СОВРЕМЕННЫЕ АВТОРЫ</span><h2>Новые истории — в книжных каталогах</h2>
        <p>Выбирай современные издания у источника. Доступность аудио, бесплатных выпусков и пробных фрагментов определяется самим сервисом.</p></div>
      <div className="books-services">{MODERN_SOURCES.map((service, index) => <a key={service.id} href={service.url} target="_blank" rel="noreferrer" className="books-service" style={{ '--service-index': index }}>
        <span className="books-service-mark">{service.mark}</span><span><strong>{service.name}</strong><small>{service.note}</small></span><b aria-hidden="true">↗</b>
      </a>)}</div>
    </section>

    {selected && <div className="books-drawer-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) closeBook(); }}>
      <section className="books-drawer" role="dialog" aria-modal="true" aria-labelledby="books-drawer-title">
        <button className="books-drawer-close" aria-label="Закрыть" onClick={closeBook}><IcoX /></button>
        <div className="books-drawer-top"><BookCover book={selected} className="books-drawer-cover" /><div><span>LIBRIVOX · ОТКРЫТАЯ ЗАПИСЬ</span><h2 id="books-drawer-title">{selected.title}</h2><p>{selected.author}</p>
          <div className="books-book-meta"><span>{selected.language}</span>{selected.duration > 0 && <span>{formatBookDuration(selected.duration)}</span>}</div>
          {bookCurrentId != null
            ? <button className="books-play-all on" onClick={() => P.toggle()} aria-label={pst.playing ? 'Пауза' : 'Продолжить'}>{pst.playing ? '❚❚  Пауза' : '▶  Продолжить'}</button>
            : <button className="books-play-all" disabled={bookBusy || !selected.chapters?.length} onClick={() => playBook(selected, selected.chapters || [])}>{bookBusy ? 'Загружаем главы…' : '▶  Слушать книгу'}</button>}
        </div></div>
        {selected.description && <p className="books-description">{selected.description}</p>}
        <div className="books-chapter-head"><span>СОДЕРЖАНИЕ</span><strong>{selected.chapters?.length || 0} глав</strong></div>
        {bookBusy && !selected.chapters ? <div className="books-more">Загружаем содержание…</div> : null}
        {selected.chapters?.length ? <div className="books-chapters">{selected.chapters.map((chapter, index) => {
          const tid = chapterTrackId(selected, chapter);
          const isCur = tid != null && tid === curTrackId;
          return <article key={chapter.id} className={'book-chapter' + (isCur ? ' on' : '')}>
          <button className="book-chapter-play" aria-label={isCur ? (pst.playing ? 'Пауза' : 'Продолжить') : `Слушать ${chapter.title}`} onClick={() => playBook(selected, selected.chapters, index)}>{isCur && pst.playing ? '❚❚' : '▶'}</button>
          <div className="book-chapter-copy"><span>{isCur ? (pst.playing ? 'ИГРАЕТ · ГЛАВА ' : 'ПАУЗА · ГЛАВА ') : 'ГЛАВА '}{chapter.number || index + 1}</span><strong>{chapter.title}</strong><small>{chapter.narrator || 'Чтец LibriVox'}{chapter.duration ? ` · ${formatBookDuration(chapter.duration)}` : ''}</small></div>
          <a className="book-chapter-download" href={`${API_BASE}${chapter.audio_url}?download=1`} aria-label={`Скачать главу ${chapter.title}`}><IcoDl /></a>
          {chapter.duration > 0 && chapter.duration <= 3600 && <button className="book-chapter-offline" disabled={!!savingId} onClick={() => saveChapter(selected, chapter)}>{savingId === chapter.id ? '…' : 'В Vibe'}</button>}
        </article>;
        })}</div> : null}
        <a className="books-credit" href={selected.catalog_url} target="_blank" rel="noreferrer">Страница записи и лицензия в LibriVox ↗</a>
      </section>
    </div>}
  </main>;
}
