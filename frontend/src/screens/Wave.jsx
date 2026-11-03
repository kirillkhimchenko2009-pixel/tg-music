/* Волна — аналог «Мои волны» Яндекса: личный бесконечный поток.
   Гибрид: движок A — rotor ЯМ (у владельцев /token), движок B — наш подбор.
   Формула в bottom-sheet: настроение, занятие, жанры, разнообразие, язык, 18+. */
import React, { useEffect, useRef, useState } from 'react';
import { waveStart, waveNext, waveFeedback, waveSettings, waveSaveSettings } from '../api.js';
import * as P from '../player.js';
import { haptic } from '../tg.js';
import { coverOr, IcoNext, PlayPause } from '../App.jsx';
import { toggleLike, useLikes } from '../likes.js';

const MOODS = [
  { id: null, icon: '∞', label: 'Любое' },
  { id: 'fun', icon: '😊', label: 'Веселье' },
  { id: 'active', icon: '⚡', label: 'Энергия' },
  { id: 'calm', icon: '🌙', label: 'Спокойствие' },
  { id: 'sad', icon: '💧', label: 'Грусть' },
  { id: 'edge', icon: '🔥', label: 'Жёсткость' },
];
const DIVERSITY = [
  { id: 'default', icon: '∅', label: 'Как есть' },
  { id: 'favorite', icon: '❤', label: 'Своё' },
  { id: 'popular', icon: '✦', label: 'Популярное' },
  { id: 'discover', icon: '✧', label: 'Новое' },
];
const LANGS = [
  { id: 'any', icon: '🌐', label: 'Любой' },
  { id: 'ru', icon: '🇷🇺', label: 'Русский' },
  { id: 'non-ru', icon: '🌍', label: 'Иностранный' },
];
const GENRES = ['hiphop', 'rock', 'pop', 'electronic', 'jazz', 'classical',
  'indie', 'shanson', 'rnb', 'folk'];
const GENRE_LABEL = {
  hiphop: 'Хип-хоп', rock: 'Рок', pop: 'Поп', electronic: 'Электронное',
  jazz: 'Джаз', classical: 'Классика', indie: 'Инди', shanson: 'Шансон',
  rnb: 'R&B', folk: 'Фолк',
};
const ACTIVITIES = [
  { id: 'work', icon: '💼', label: 'Работа' },
  { id: 'sport', icon: '🏃', label: 'Спорт' },
  { id: 'road', icon: '🚗', label: 'Дорога' },
  { id: 'walk', icon: '🚶', label: 'Прогулка' },
  { id: 'party', icon: '🎉', label: 'Вечеринка' },
  { id: 'evening', icon: '🌆', label: 'Вечер' },
];
const ENGINES = [
  { id: 'auto', label: 'Авто', hint: 'волна ЯМ, где есть токен' },
  { id: 'yandex', label: 'Волна ЯМ', hint: 'настоящий rotor Яндекса' },
  { id: 'own', label: 'Свой движок', hint: 'подбор по нашей БД' },
];

const HEART_D = 'M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3'
  + 'c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5'
  + 'c0 3.78-3.4 6.86-8.55 11.54L12 21.35z';

export default function Wave() {
  const st = usePlayerSnap();
  const [phase, setPhase] = useState('boot');   // boot | ready | err
  const [err, setErr] = useState('');
  const [ws, setWs] = useState(null);           // формула (settings DTO)
  const [sess, setSess] = useState(null);       // { session_id, engine, skips_* }
  const [sheet, setSheet] = useState(false);
  const [dirty, setDirty] = useState(false);    // формула изменилась — жмёт перезапуск
  const booted = useRef(false);

  const startWave = async (engine) => {
    setPhase('boot'); setErr('');
    try {
      const j = await waveStart(engine || ws?.engine || 'auto');
      setSess({ session_id: j.session_id, engine: j.engine,
        skips_per_hour: j.skips_per_hour, skips_used: j.skips_used || 0 });
      // порядок важен: play() сбрасывает wave-источник — волну включаем ПОСЛЕ
      await P.play(j.tracks || [], 0);
      P.setWaveSource({
        sessionId: j.session_id,
        fetchMore: async (last) => {
          const r = await waveNext(j.session_id, last);
          if (r.skips_per_hour !== undefined) setSess(p => ({ ...p,
            skips_per_hour: r.skips_per_hour, skips_used: r.skips_used || 0 }));
          return r.tracks || [];
        },
        onEvent: (kind, track, played) => {
          if (kind === 'started') waveFeedback(j.session_id, 'trackStarted', track.id).catch(() => {});
          else if (kind === 'skip') waveFeedback(j.session_id, 'skip', track.id, played).catch(() => {});
          else if (kind === 'finished') waveFeedback(j.session_id, 'trackFinished', track.id, played).catch(() => {});
        },
      });
      setPhase('ready');
    } catch (e) {
      setErr((e && e.message) || 'волна не подала признаков жизни');
      setPhase('err'); haptic('heavy');
    }
  };

  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    (async () => {
      let formula = null;
      try { formula = await waveSettings(); setWs(formula); } catch { /* формула по умолчанию */ }
      const snap = P.getState();
      if (P.inWave() && snap.queue.length) { setPhase('ready'); return; }
      if (!snap.playing) { await startWave(formula?.engine || 'auto'); }
      else setPhase('ready');   // играет обычная очередь — не перехватываем
    })();
  }, []);

  /* live-обновление счётчика скипов из ответов /next приходит через setSess; */
  const cur = st.queue[st.index];
  const pct = st.duration ? (st.currentTime / st.duration) * 100 : 0;
  const skipsSoon = !!sess?.skips_per_hour
    && (sess.skips_used || 0) + 1 >= sess.skips_per_hour;

  const dislike = () => {
    haptic('medium');
    if (sess && cur) waveFeedback(sess.session_id, 'dislike', cur.id, st.currentTime).catch(() => {});
    P.next();
  };

  const patchFormula = async (patch) => {
    haptic('select');
    try {
      const j = await waveSaveSettings(patch);
      setWs(j); setSheet(false); setDirty(true);
    } catch { /* тихая неудача настройки */ }
  };

  return (
    <div className="waves">
      <div className="wave-aura" aria-hidden="true" />

      <div className="wave-head">
        <div className="wave-title">Волна</div>
        <button className={'wave-formula' + (dirty ? ' dirty' : '')}
                onClick={() => { haptic('soft'); setSheet(true); }}>
          {formulaText(ws)}<span className="wave-gear">⛭</span>
        </button>
      </div>

      {phase === 'ready' && cur ? <>
        <div className="covin wave-covwrap" key={'c' + cur.id}>
          <WaveCover track={cur} playing={st.playing} />
          {st.playing && <span className="eq eq-lg"><i /><i /><i /></span>}
        </div>

        <div className="wave-meta" key={cur.id}>
          <div className="wave-tname">{cur.title}</div>
          <div className="wave-tartist">{cur.artist}</div>
        </div>

        {cur.why && <div className="wave-why" key={'w' + cur.id}>
          <span className="np-dot" /> {cur.why}</div>}

        <div className="seekrow wave-seek" onClick={e => e.stopPropagation()}>
          <span className="np-time">{P.fmt(st.currentTime)}</span>
          <input className="seek" type="range" min="0" max="1000"
                 value={Math.round(pct * 10)}
                 style={{ '--fill': pct.toFixed(1) + '%' }}
                 onChange={e => P.seek(+e.target.value / 1000)} />
          <span className="np-time">{P.fmt(st.duration)}</span>
        </div>

        <div className="wave-ctrl">
          <button className="cbtn glass wave-no" aria-label="Не нравится"
                  onClick={dislike} disabled={!cur}>
            <WaveNoIcon />
          </button>
          <WaveHeart id={cur.id} big session={sess?.session_id} />
          <button className="cbtn big wave-next" aria-label="Другой трек"
                  onClick={() => { haptic('medium'); P.next(); }}>
            <IcoNext />
          </button>
          <button className="cbtn big" aria-label="Играть/пауза"
                  onClick={() => { haptic('medium'); P.toggle(); }}>
            <PlayPause playing={st.playing} loading={st.loading} big />
          </button>
        </div>

        <WaveHistory queue={st.queue} index={st.index} />

        <div className="wave-foot">
          <span className={'wave-engine ' + (sess?.engine === 'yandex' ? 'ym' : 'own')}>
            {sess?.engine === 'yandex' ? '⚡ Волна ЯМ' : '◈ Свой движок'}
          </span>
          {sess?.skips_per_hour ? (
            <span className={'skips' + (skipsSoon ? ' soon' : '')}>
              Скипы: {sess.skips_used || 0}/{sess.skips_per_hour} в час
            </span>) : null}
          {st.loading && <span className="minispinner" />}
        </div>
      </> : phase === 'err' ? <>
        <div className="cardbox">
          <div className="login">
            <h2>Волна тихая…</h2>
            <p className="muted">{err}</p>
            <button className="bigbtn primary" onClick={() => startWave(ws?.engine)}>Ещё раз</button>
          </div>
        </div>
      </> : <>
        <div className="boot-eq wave-boot"><i /><i /><i /></div>
        <p className="muted wave-boottext">Настраиваю волну…</p>
      </>}

      {sheet && ws && <FormulaSheet ws={ws} dirty={dirty}
        onClose={() => setSheet(false)} onChange={patchFormula}
        onRestart={() => { setSheet(false); setDirty(false); startWave(ws.engine); }} />}
    </div>
  );
}

function formulaText(ws) {
  if (!ws) return 'Формула';
  const parts = [];
  const m = MOODS.find(x => x.id === ws.mood);
  if (m) parts.push(m.label);
  for (const g of (ws.genres || [])) parts.push(GENRE_LABEL[g] || g);
  for (const a of (ws.artists || [])) parts.push(a.name || a);
  if (!parts.length) return 'Персональная';
  return parts.join(' · ');
}

function WaveCover({ track, playing }) {
  const [broken, setBroken] = useState(false);
  const src = coverOr(track);
  if (!src || broken) {
    return <div className="wave-cover ghost"><span>≋</span></div>;
  }
  return (
    <img className={'wave-cover' + (playing ? ' kb' : '')} src={src} alt=""
         onError={() => setBroken(true)} />
  );
}

/* Недавно в волне: отыгранные треки сессии. Клик — вернуться к треку. */
function WaveHistory({ queue, index }) {
  const prev = queue.slice(Math.max(0, index - 8), index).reverse();
  if (!prev.length) return null;
  return (
    <div className="wave-hist">
      <div className="wave-hist-t">Недавно в волне</div>
      <div className="wave-hist-row">
        {prev.map((t, i) => (
          <button key={(t.id || 'x') + '-' + i} className="wave-hist-chip" title={t.title}
                  aria-label={`Вернуться: ${t.artist} — ${t.title}`}
                  onClick={() => {
                    haptic('soft');
                    const qi = queue.findIndex(x => x.id === t.id);
                    if (qi >= 0) P.play([...queue], qi);
                  }}>
            {t.cover_url
              ? <img src={coverOr(t)} alt=""
                     onError={e => { e.currentTarget.style.visibility = 'hidden'; }} />
              : <span className="wave-hist-glyph">≋</span>}
          </button>
        ))}
      </div>
    </div>
  );
}

function WaveHeart({ id, session, big }) {
  const liked = useLikes().has(id);
  return (
    <button className={'heart' + (liked ? ' on' : '') + (big ? ' lg' : '')} aria-label="В любимое"
            onClick={async (e) => {
              e.stopPropagation();
              haptic(liked ? 'soft' : 'light');
              const nowLiked = await toggleLike(id);
              if (session) waveFeedback(session, nowLiked ? 'like' : 'unlike', id).catch(() => {});
            }}>
      <svg viewBox="0 0 24 24"><path d={HEART_D} /></svg>
    </button>
  );
}

function WaveNoIcon() {
  return (
    <svg viewBox="0 0 24 24" className="ico" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round">
      <path d="M17 4l1.5 3M7 4L5.5 7M12 4v4" /><circle cx="12" cy="16" r="4.5" />
      <path d="M4 4l16 16" style={{ stroke: '#FF6D6D' }} />
    </svg>
  );
}

/* ---------------- формулы bottom-sheet ---------------- */
function FormulaSheet({ ws, dirty, onClose, onChange, onRestart }) {
  const chipOn = (cond) => 'chip' + (cond ? ' on' : '');
  const cardRef = useRef(null);
  const drag = useRef({ start: 0, dy: 0, active: false });
  const [ty, setTy] = useState(0);
  const [held, setHeld] = useState(false);

  /* Esc для десктопа; на мобиле — свайп-вниз по карточке */
  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  const onTS = (e) => {
    const card = cardRef.current;
    if (card && card.scrollTop > 2) return;   // прокрутка контента важнее жеста
    drag.current = { start: e.touches[0].clientY, dy: 0, active: true };
    setHeld(true);
  };
  const onTM = (e) => {
    if (!drag.current.active) return;
    const dy = e.touches[0].clientY - drag.current.start;
    if (dy > 0) { drag.current.dy = dy; setTy(dy); }
  };
  const onTE = () => {
    if (!drag.current.active) return;
    drag.current.active = false;
    setHeld(false);
    if (drag.current.dy > 92) onClose(); else setTy(0);
  };

  return (
    <div className="wsheet" onClick={onClose}>
      <div className="wsheet-card" ref={cardRef} onClick={e => e.stopPropagation()}
           onTouchStart={onTS} onTouchMove={onTM} onTouchEnd={onTE}
           style={{ transform: ty ? `translateY(${ty}px)` : undefined,
                    transition: held ? 'none' : 'transform .3s var(--spring)' }}>
        <div className="wsheet-grabber" aria-hidden="true" />
        <div className="wsheet-head">
          <div className="wsheet-title">Формула волны</div>
          <button className="wsheet-x" onClick={onClose}>✕</button>
        </div>
        {dirty && <button className="wave-dirty" onClick={onRestart}
          >Формула обновилась — перезапусти волну ⟳</button>}

        <div className="wsec">
          <div className="wsec-t">Настроение</div>
          <div className="chiprow">
            {MOODS.map(m => (
              <button key={String(m.id)} className={chipOn((ws.mood ?? null) === m.id)}
                      onClick={() => onChange({ mood: m.id })} disabled={m.id === null && ws.mood === null}>
                {m.icon} {m.label}
              </button>))}
          </div>
        </div>

        <div className="wsec">
          <div className="wsec-t">Разнообразие</div>
          <div className="chiprow">
            {DIVERSITY.map(v => (
              <button key={v.id} className={chipOn((ws.diversity || 'default') === v.id)}
                      onClick={() => onChange({ diversity: v.id })}>
                {v.icon} {v.label}
              </button>))}
          </div>
        </div>

        <div className="wsec">
          <div className="wsec-t">Занятие</div>
          <div className="chiprow">
            {ACTIVITIES.map(a => (
              <button key={a.id} className={chipOn((ws.activities || []).includes(a.id))}
                      onClick={() => onChange({
                        activities: ((ws.activities || []).includes(a.id)
                          ? (ws.activities || []).filter(x => x !== a.id)
                          : [...(ws.activities || []), a.id]) })}>
                {a.icon} {a.label}
              </button>))}
          </div>
        </div>

        <div className="wsec">
          <div className="wsec-t">Жанры</div>
          <div className="chiprow">
            {GENRES.map(g => (
              <button key={g} className={chipOn((ws.genres || []).includes(g))}
                      onClick={() => onChange({
                        genres: ((ws.genres || []).includes(g)
                          ? (ws.genres || []).filter(x => x !== g)
                          : [...(ws.genres || []), g]) })}>
                {GENRE_LABEL[g]}
              </button>))}
          </div>
        </div>

        <div className="wsec">
          <div className="wsec-t">Язык</div>
          <div className="chiprow">
            {LANGS.map(v => (
              <button key={v.id} className={chipOn((ws.language || 'any') === v.id)}
                      onClick={() => onChange({ language: v.id })}>
                {v.icon} {v.label}
              </button>))}
          </div>
        </div>

        <div className="wsec row3">
          <div>
            <div className="wsec-t">Треки 18+</div>
            <div className="chiprow">
              <button className={chipOn(ws.allow_explicit)}
                      onClick={() => onChange({ allow_explicit: true })}>Разрешено</button>
              <button className={chipOn(!ws.allow_explicit)}
                      onClick={() => onChange({ allow_explicit: false })}>Без 18+</button>
            </div>
          </div>
          <div>
            <div className="wsec-t">Движок</div>
            <div className="chiprow">
              {ENGINES.map(v => (
                <button key={v.id} className={chipOn((ws.engine || 'auto') === v.id)}
                        title={v.hint} onClick={() => onChange({ engine: v.id })}>
                  {v.label}
                </button>))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
