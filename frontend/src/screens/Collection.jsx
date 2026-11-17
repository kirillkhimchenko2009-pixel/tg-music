/* Коллекция: «Любимое», мои/импортированные плейлисты, альбомы,
   исполнители, история — самоё «библиотека» Vibe.
   Экран плейлиста (PlaylistScreen) публичен: открывается и со «Главная». */
import React, { useEffect, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { api, API_BASE } from '../api.js';
import * as P from '../player.js';
import { haptic } from '../tg.js';
import {
  Rail, PlayCard, ArtistCard, SectionHead, TrackRow, TrackMenu, Skeletons,
  FadeImg, fmtTime, plural, groupAlbums, gradFor, dominantColor, coverOr,
  IcoHeart, IcoPlus, IcoDl, IcoChevR, IcoNote, IcoCheck, IcoX, IcoWave,
} from '../ui.jsx';
import { addLikesBulk, loadLikes } from '../likes.js';
import * as routes from '../routes.js';
import { Sheet, toast } from '../motion/index.js';
import Reveal from '../motion/components/Reveal.jsx';

const cimg = u => (!u ? '' : (u.startsWith('http') ? u : API_BASE + u));

export default function Collection({ sub }) {
  const [items, setItems] = useState(null);
  const [open, setOpen] = useState(null);          // открытый плейлист (id)
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState('');

  const reload = () => api('/api/playlists')
    .then(d => setItems(d.playlists || []))
    .catch(e => setErr(e.message));

  useEffect(() => {
    reload();
  }, [sub]);

  // sub: null | playlists | likes | albums | artists | history
  const likes = items?.find(p => /любимое/i.test(p.title || ''));
  const mine = useMemo(() => (items || []).filter(p => !/любимое/i.test(p.title || '')), [items]);

  useEffect(() => {
    if (sub === 'likes' && likes) setOpen(likes.id);
  }, [sub, likes?.id]);

  if (err) return <div className="err">{err}</div>;

  if (open) return <PlaylistScreen plId={open} back={() => setOpen(null)} />;

  if (!items) return <div style={{ padding: '18px 2px' }}><Skeletons n={6} grid /></div>;

  const list = mine;

  return (
    <div>
      <h1 className="h">Моя коллекция</h1>
      <div className="sub">Плейлисты, альбомы, исполнители — всё, что собрал.</div>

      {/* ❤ Любимое — главный ряд */}
      {likes && (
        <button className="likesrow rise" onClick={() => { haptic('light'); setOpen(likes.id); }}>
          <span className="likescov" style={{ '--g': 'linear-gradient(140deg,#42504a,#20282a)' }}>
            <svg viewBox="0 0 24 24" width="30" height="30" fill="#D9ED3A">
              <path d="M12 21c-4.6-4-8.7-7-8.7-10.8C3.3 7 5.6 4.7 8.4 4.7c1.6 0 3.1.8 4.1 2 1-1.2 2.4-2 4-2 2.8 0 5.1 2.3 5.1 5.5 0 3.8-4.1 6.8-8.7 10.8z" />
            </svg>
          </span>
          <span className="likesmeta">
            <span className="likest">Любимое</span>
            <span className="likesn">{plural(likes.count, 'трек', 'трека', 'треков')} · собрано сердцами ♥</span>
          </span>
          <span className="chevr"><IcoChevR /></span>
        </button>
      )}

      {/* Альбомы-секция: только на sub=albums */}
      {(sub === 'albums' || sub === null) && <AlbumsSection compact={sub === null} />}

      {/* Исполнители: sub=artists или обзор */}
      {(sub === 'artists' || sub === null) && <ArtistsSection compact={sub === null} />}

      {/* История */}
      {sub === 'history' && <HistorySection />}

      {/* Плейлисты */}
      {sub === 'playlists' || sub === null ? (
        <section>
          <SectionHead title={sub === 'playlists' ? 'Плейлисты' : 'Мои плейлисты'}
                       action={sub === null ? 'Все' : null}
                       onAction={() => routes.goTab('collection', 'playlists')} />
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key="list" className="albgrid"
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: .22, ease: [.16, 1, .3, 1] }}>
              <button className="newpl" onClick={() => { haptic('medium'); setCreating(true); }}>
                <span className="np-ic"><IcoPlus width="1em" height="1em" /></span>
                <span className="np-t"><span>Новый плейлист</span>
                  <span className="np-s">Собрать список</span></span>
              </button>
              {list.map((p, i) => (
                <PlayCard key={p.id} title={(p.title || '').replace(/^❤\s*/, '')}
                          sub={plural(p.count, 'трек', 'трека', 'треков')}
                          onOpen={() => { haptic('light'); setOpen(p.id); }}
                          t={p.cover_url ? { cover_url: p.cover_url } : null}
                          grad={gradFor(p.title)}
                          cellDelay={Math.min(i, 6) * 40} />
              ))}
            </motion.div>
          </AnimatePresence>
          {(sub === 'playlists' && list.length === 0) && (
            <div className="muted" style={{ marginTop: 14 }}>
              Собери первый список — кнопка «Новый плейлист», или перенеси
              готовый через «Импорт»: он появится здесь же.
            </div>
          )}
        </section>
      ) : null}

      <CreateSheet open={creating}
                   onClose={() => setCreating(false)}
                   onCreated={() => { setCreating(false); reload(); }} />
    </div>
  );
}

/* новый плейлист (грид-плитка) — стиль в theme.css (.newpl) */

/* ---------------- секция альбомов ---------------- */
function AlbumsSection({ compact }) {
  const [lib, setLib] = useState(null);
  useEffect(() => {
    api('/api/library?limit=300').then(d => setLib(d.tracks || [])).catch(() => setLib([]));
  }, []);
  if (!lib) return <Skeletons n={5} grid />;
  const albums = groupAlbums(lib);
  if (!albums.length) return null;

  const show = albums.slice(0, compact ? 8 : 40);
  return (
    <Reveal>
      <SectionHead title="Альбомы" action={compact ? 'Все' : null}
                   onAction={compact ? () => routes.goTab('collection', 'albums') : null} />
      {compact ? (
        <Rail>
          {show.map((a, i) => (
            <PlayCard key={a.key} title={a.title} sub={a.artist}
                      onOpen={() => routes.goAlbum(a)}
                      onPlay={() => P.play(a.tracks, 0)} cellDelay={Math.min(i, 6) * 40} />
          ))}
        </Rail>
      ) : (
        <div className="albgrid">
          {show.map((a, i) => (
            <PlayCard key={a.key} title={a.title} sub={`${a.artist}${a.year ? ' · ' + a.year : ''}`}
                      onOpen={() => routes.goAlbum(a)}
                      onPlay={() => P.play(a.tracks, 0)} cellDelay={Math.min(i, 6) * 40} />
          ))}
        </div>
      )}
    </Reveal>
  );
}

/* ---------------- исполнители ---------------- */
function ArtistsSection({ compact }) {
  const [artists, setArtists] = useState(null);
  useEffect(() => {
    api('/api/artists/top?limit=14').then(d => setArtists(d.artists || [])).catch(() => setArtists([]));
  }, []);
  if (!artists) return null;                       // без скелетонов: пустая секция лишняя
  if (!artists.length) return null;                // честно скрываем, если пусто

  const show = artists.slice(0, compact ? 10 : 20);
  return (
    <Reveal>
      <SectionHead title="Исполнители" action={compact ? 'Все' : null}
                   onAction={compact ? () => routes.goTab('collection', 'artists') : null} />
      {compact ? (
        <Rail>
          {show.map((a, i) => (
            <ArtistCard key={a.name} name={a.name} cover={a.cover_url}
                        sub={plural(a.count, 'трек', 'трека', 'треков')}
                        onOpen={() => routes.goArtist(a.name, a.cover_url)} delay={Math.min(i, 6) * 40} />
          ))}
        </Rail>
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '18px 14px' }}>
          {show.map((a, i) => (
            <ArtistCard key={a.name} name={a.name} cover={a.cover_url}
                        sub={plural(a.count, 'трек', 'трека', 'треков')}
                        onOpen={() => routes.goArtist(a.name, a.cover_url)} delay={Math.min(i, 6) * 40} />
          ))}
        </div>
      )}
    </Reveal>
  );
}

/* ---------------- история ---------------- */
function HistorySection() {
  const [tracks, setTracks] = useState(null);
  useEffect(() => {
    api('/api/history?limit=40').then(d => setTracks(d.tracks || [])).catch(() => setTracks([]));
  }, []);
  if (!tracks) return <Skeletons n={6} />;
  if (!tracks.length) return (
    <div className="empty">
      <div className="big float">🕰</div>
      История пуста: послушай волну или любой плейлист — недавнее здесь появится.
    </div>
  );
  return (
    <div className="tracks">
      {tracks.map((t, i) => (
        <TrackRow key={t.id} t={t} i={i} num={i + 1} onPlay={() => P.play(tracks, i)} />
      ))}
    </div>
  );
}

/* ---------------- шторка создания ---------------- */
function CreateSheet({ open, onClose, onCreated }) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const ref = React.useRef(null);
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => ref.current?.focus(), 160);
    return () => clearTimeout(t);
  }, [open]);
  const submit = async () => {
    const title = name.trim();
    if (!title || busy) return;
    setBusy(true);
    setErr('');
    try {
      await api('/api/playlists', { method: 'POST', body: JSON.stringify({ title }) });
      haptic('ok');
      toast('Плейлист создан', { kind: 'success' });
      setName('');
      onCreated();
    } catch (e) { setErr(e.message || 'Не получилось'); setBusy(false); }
  };
  return (
    <Sheet open={open} onClose={onClose} labelledBy="create-pl-title">
      <div className="sht" id="create-pl-title">Новый плейлист</div>
      <input ref={ref} className="shinput" placeholder="Название плейлиста"
             value={name} maxLength={120}
             onChange={e => setName(e.target.value)}
             onKeyDown={e => { if (e.key === 'Enter') submit(); }} />
      {err && <div className="err" style={{ marginTop: 10 }}>{err}</div>}
      <div className="btnrow">
        <button className="bigbtn" onClick={onClose}>Отмена</button>
        <button className="bigbtn primary" disabled={!name.trim() || busy} onClick={submit}>
          {busy ? <span className="minispinner" /> : 'Создать'}
        </button>
      </div>
    </Sheet>
  );
}

/* ================= ЭКРАН ПЛЕЙЛИСТА (публичный) ================= */
export function PlaylistScreen({ plId, pl: plKnown, back }) {
  const [meta, setMeta] = useState(plKnown || null);      // {id,title,cover_url,...}
  const [tracks, setTracks] = useState(null);
  const [prog, setProg] = useState(null);
  const [menu, setMenu] = useState(null);                // трек для меню
  const [glow, setGlow] = useState(null);
  const [err, setErr] = useState('');
  const [favBusy, setFavBusy] = useState(false);         // «Всё в любимое»
  /* Поэтапный маунт: «Любимое» может быть 500+ строк — рендер всех в один
     кадр подвешивал переход на страницу. Первые ~120 сразу, остальные
     добираются чанками в свободных кадрах (визуально незаметно). */
  const [shown, setShown] = useState(120);
  useEffect(() => { setShown(120); }, [plId]);
  useEffect(() => {
    const total = tracks?.length || 0;
    if (shown >= total) return undefined;
    const t = setTimeout(() => setShown(n => Math.min(n + 120, total)), 0);
    return () => clearTimeout(t);
  }, [tracks, shown]);

  useEffect(() => {
    api('/api/playlists').then(d => {
      const pl = (d.playlists || []).find(p => p.id === plId);
      if (pl) setMeta(m => m && m.id === plId ? { ...m, ...pl } : m || pl);
    }).catch(() => {});
  }, [plId]);

  const reload = () => api(`/api/playlists/${plId}/tracks`)
    .then(d => setTracks(d.tracks || []))
    .catch(e => setErr(e.message));
  useEffect(() => { reload(); }, [plId]);

  useEffect(() => {
    let dead = false;
    dominantColor(cimg(meta?.cover_url)).then(rgb => {
      if (!dead && rgb) setGlow(`rgb(${rgb[0]},${rgb[1]},${rgb[2]})`);
    });
    return () => { dead = true; };
  }, [meta?.cover_url]);

  const downloadAll = async () => {
    if (!tracks?.length || prog) return;
    haptic('medium');
    /* «Скачать всё» для длинных списков: бэкенд молча режет пачку до
       WEBAPP_BULK_MAX (200) — тот же класс бага, что «Всё в любимое».
       Шлём чанками по 200 ПОСЛЕДОВАТЕЛЬНО (параллельные INSERT-ы на Neon
       плодят «database is locked») и агрегируем ответ; поллинг статусов
       тоже чанками (кап jobs-bulk — 400 id на запрос). */
    const ids = [...new Set(tracks.map(t => t.id))];
    const CHUNK = 200;
    const jobs = [];
    let skipped = 0;
    setProg({ done: 0, total: ids.length });
    try {
      for (let i = 0; i < ids.length; i += CHUNK) {
        const res = await api('/api/download-bulk', {
          method: 'POST',
          body: JSON.stringify({ track_ids: ids.slice(i, i + CHUNK) }),
        });
        jobs.push(...(res.jobs || []).map(j => j.job));
        skipped += (res.skipped || []).length;
        setProg({ done: 0, total: jobs.length + skipped });
      }
    } catch (e) {
      setProg(null);
      if (e.message === 'telegram_required') return;
      /* джобы уже поставленных чанков воркер доработает без нас */
      if (jobs.length) {
        toast(`Поставлено ${jobs.length} из ${ids.length} — остальное позже`, { kind: 'error' });
        return;
      }
      toast('Не удалось поставить загрузку: ' + e.message, { kind: 'error' });
      return;
    }
    if (!jobs.length) {
      setProg(null);
      toast(skipped ? 'Всё уже скачано — повторно не качаю'
                    : 'Нечего скачивать', { kind: 'success' });
      return;
    }
    const total = jobs.length;
    const PCHUNK = 350;                        // бэкенд-кап поллинга — 400
    let settled = 0, done = 0;
    for (let it = 0; it < 700; it++) {
      await new Promise(r => setTimeout(r, 2000));
      const merged = {};
      let polled = true;
      for (let i = 0; i < jobs.length; i += PCHUNK) {
        try {
          const st = await api('/api/jobs-bulk?ids=' + jobs.slice(i, i + PCHUNK).join(','));
          Object.assign(merged, st.jobs || {});
        } catch { polled = false; break; }
      }
      if (!polled) continue;
      const vals = Object.values(merged);
      done = vals.filter(s => s === 'done').length;
      settled = vals.filter(s => s === 'done' || s === 'error' || s === 'canceled').length;
      setProg({ done, total: total + skipped, fromCache: skipped });
      if (settled >= total) break;
    }
    haptic('ok');
    const left = Math.max(0, total - settled);
    toast(left ? `В очереди: ${left} — файлы придут в чат по мере готовности`
               : (skipped ? `Готово: ${total} новых · из кеша: ${skipped}`
                          : 'Плейлист скачан в чат'),
          { kind: 'success' });
    setProg(null);
  };

  const totalDur = (tracks || []).reduce((s, t) => s + (t.duration || 0), 0);
  const isLikesPl = /любимое/i.test(meta?.title || '');
  const likeAll = async () => {
    if (!tracks?.length || favBusy) return;
    haptic('medium');
    setFavBusy(true);
    try {
      const j = await addLikesBulk(tracks.map(t => t.id));
      haptic('ok');
      toast(j.added ? `В «Любимом»: +${j.added}` : 'Всё уже в любимом',
            { kind: 'success' });
    } catch (e) {
      toast(e.message || 'Не получилось', { kind: 'error' });
    }
    setFavBusy(false);
  };

  return (
    <div>
      {back && (
        <div className="page-head" style={{ marginBottom: 0 }}>
          <button className="page-back" onClick={back}>‹ Коллекция</button>
        </div>
      )}
      <motion.div
        className="ahero-card"
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: .32, ease: [.16, 1, .3, 1] }}
        style={{ '--g1': glow || '#2a2d35', marginTop: back ? 12 : 8 }}
      >
        <div className="ahero-top">
          <span className="ahero-cover">
            {meta?.cover_url
              ? <FadeImg src={cimg(meta.cover_url)} alt="" />
              : <span className="cph" style={{ background: gradFor(meta?.title || 'pl') }}><IcoNote width="1em" height="1em" style={{ width: '30%', height: '30%' }} /></span>}
          </span>
          <div className="ahero-meta">
            <div className="ahero-kind">Плейлист</div>
            <div className="ahero-name">{(meta?.title || '…').replace(/^❤\s*/, '')}</div>
            <div className="ahero-note">
              <span>{meta ? plural(meta.count ?? (tracks?.length || 0), 'трек', 'трека', 'треков') : ''}</span>
              {totalDur > 0 && <span>· {fmtTime(totalDur)}</span>}
            </div>
          </div>
        </div>
        <div className="ahero-cta">
          <button className="pbtn-lg" aria-label="Слушать плейлист"
                  onClick={() => { haptic('medium'); tracks && tracks.length && P.play(tracks, 0); }}>
            <svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22"><path d="M8.2 5.6v12.8L18.6 12z" /></svg>
          </button>
          <button className="bigbtn"
                  onClick={() => { haptic('medium'); if (tracks?.length) { const shuffled = [...tracks].sort(() => Math.random() - .5); P.play(shuffled, 0); } }}>
            <IcoWave width="15" height="15" /> Перемешать
          </button>
          <button className="iconact" onClick={downloadAll} aria-label="Скачать всё" disabled={!!prog}>
            {prog ? <span className="minispinner" /> : <IcoDl />}
          </button>
          {!isLikesPl && (
            <button className="iconact" onClick={likeAll}
                    aria-label="Всё в любимое" disabled={favBusy || !tracks?.length}>
              {favBusy ? <span className="minispinner" /> : <IcoHeart />}
            </button>
          )}
        </div>
        {prog && <div className="np-chip" style={{ marginTop: 12 }}><span className="np-dot" /> Скачиваю: {prog.done}/{prog.total}</div>}
        {err && <div className="err" style={{ marginTop: 12 }}>{err}</div>}
      </motion.div>

      {!tracks && <Skeletons n={5} />}
      {tracks && !tracks.length && (
        <div className="empty">
          <div className="big float">♪</div>
          Пока пусто. Добавляй треки из поиска (♥ и меню ⋮) — они сложатся сюда.
        </div>
      )}
      <div className="tracks">
        {(tracks || []).slice(0, shown).map((t, i) => (
          <TrackRow key={t.id + ':' + t.pt_id} t={t} i={i} num={i + 1} time={t.duration}
                    onPlay={() => P.play(tracks, i)}
                    onMenu={() => setMenu(t)} />
        ))}
      </div>
      <TrackMenu t={menu} open={!!menu} onClose={() => setMenu(null)}
                 playlistId={plId} ptId={menu?.pt_id} onChanged={reload}
                 onOpenArtist={name => { setMenu(null); routes.goArtist(name, menu.cover_url); }} />
    </div>
  );
}
