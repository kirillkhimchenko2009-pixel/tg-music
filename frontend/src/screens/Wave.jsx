/* Волна — аналог «Мои волны» Яндекса: личный бесконечный поток.
   Гибрид: движок A — rotor ЯМ (у владельцев /token), движок B — наш подбор.
   Формула в bottom-sheet: настроение, занятие, жанры, разнообразие, язык, 18+. */
import React, { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useAnimationControls } from 'motion/react';
import { api, waveStart, waveNext, waveFeedback, waveSettings, waveSaveSettings } from '../api.js';
import * as P from '../player.js';
import { usePlayerSnap } from '../player.js';
import { haptic } from '../tg.js';
import {
  coverOr, FadeImg, Eq, IcoCheck, IcoDownload, IcoNext, PlayPause,
} from '../ui.jsx';
import { toggleLike, useLikes } from '../likes.js';
import { AnimatedIcon, Seek, Sheet, toast } from '../motion/index.js';

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

const EASE = [0.16, 1, 0.3, 1];

export default function Wave() {
  const st = usePlayerSnap();
  const [phase, setPhase] = useState('boot');   // boot | ready | err
  const [err, setErr] = useState('');
  const [ws, setWs] = useState(null);           // формула (settings DTO)
  const [sess, setSess] = useState(null);       // { session_id, engine }
  const [sheet, setSheet] = useState(false);
  const [dirty, setDirty] = useState(false);    // формула изменилась — жмёт перезапуск
  const booted = useRef(false);

  const startWave = async (engine) => {
    setPhase('boot'); setErr('');
    try {
      const j = await waveStart(engine || ws?.engine || 'auto');
      setSess({ session_id: j.session_id, engine: j.engine });
      // порядок важен: play() сбрасывает wave-источник — волну включаем ПОСЛЕ
      await P.play(j.tracks || [], 0);
      P.setWaveSource({
        sessionId: j.session_id,
        fetchMore: async (last) => {
          const r = await waveNext(j.session_id, last);
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

  const cur = st.queue[st.index];

  const dislike = () => {
    haptic('medium');
    if (sess && cur) waveFeedback(sess.session_id, 'dislike', cur.id, st.currentTime).catch(() => {});
    P.next();
  };

  /* Скачать трек в чат (тот же путь, что кнопка ⤓ в полном плеере) */
  const [dl, setDl] = useState('');
  const download = async () => {
    if (!cur || dl === 'busy') return;
    haptic('medium');
    setDl('busy');
    let fin = '';
    try {
      const { job } = await api('/api/download?track_id=' + cur.id, { method: 'POST' });
      let it = 0;
      // джобы теперь быстрые; после ~3.5 мин перестаём крутить спиннер —
      // файл всё равно готовится на сервере и придёт сообщением в чат
      while (it++ < 175) {
        const s2 = await api('/api/jobs/' + job);
        if (s2.status === 'done') { fin = 'done'; break; }
        if (s2.status === 'error') { fin = 'err'; break; }
        await new Promise(r => setTimeout(r, 1200));
      }
      if (!fin) fin = 'slow';
    } catch { fin = 'err'; }

    if (fin === 'done') toast('Файл отправлен в чат', { kind: 'success' });
    else if (fin === 'err') toast('Не удалось скачать трек', { kind: 'error' });
    else toast('Файл ещё готовится — придёт в чат', { kind: 'info' });

    setDl(fin === 'done' ? 'done' : fin === 'err' ? 'err' : '');
    setTimeout(() => setDl(''), 4000);
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
        <motion.div className="wave-title"
                   initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                   transition={{ duration: .28, ease: EASE }}>Волна</motion.div>
        <motion.button className={'wave-formula' + (dirty ? ' dirty' : '')}
                       whileTap={{ scale: .965 }}
                       initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                       transition={{ duration: .28, ease: EASE, delay: .04 }}
                       onClick={() => { haptic('soft'); setSheet(true); }}>
          {formulaText(ws)}<span className="wave-gear">⛭</span>
        </motion.button>
      </div>

      <AnimatePresence mode="wait" initial={false}>
        {phase === 'ready' && cur ? (
          <motion.div key="wave-ready" className="wave-stage"
                      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                      transition={{ duration: .22, ease: EASE }}>
            <div className="wave-covwrap">
              <AnimatePresence mode="popLayout" initial={false}>
                <motion.div
                  key={cur.id}
                  className="wave-covin"
                  initial={{ opacity: 0, scale: .93, rotate: -1.1 }}
                  animate={{ opacity: 1, scale: 1, rotate: 0 }}
                  exit={{ opacity: 0, scale: 1.04, rotate: 1.1 }}
                  transition={{ duration: .36, ease: EASE }}
                >
                  <WaveCover track={cur} playing={st.playing} />
                </motion.div>
              </AnimatePresence>
              <AnimatePresence>
                {st.playing && (
                  <motion.span key="eq" className="wave-eq"
                               initial={{ opacity: 0, scale: .7 }}
                               animate={{ opacity: 1, scale: 1 }}
                               exit={{ opacity: 0, scale: .7 }}
                               transition={{ duration: .18, ease: EASE }}>
                    <Eq big />
                  </motion.span>
                )}
              </AnimatePresence>
            </div>

            <div className="wave-meta">
              <AnimatePresence mode="popLayout" initial={false}>
                <motion.div
                  key={cur.id}
                  initial={{ opacity: 0, y: 7 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -7 }}
                  transition={{ duration: .22, ease: EASE }}
                >
                  <div className="wave-tname">{cur.title}</div>
                  <div className="wave-tartist">{cur.artist}</div>
                  {cur.why && (
                    <div className="wave-why"><span className="np-dot" /> {cur.why}</div>
                  )}
                </motion.div>
              </AnimatePresence>
            </div>

            <div className="seekrow wave-seek">
              <Seek showTime timeClass="np-time" ariaLabel="Позиция волны" />
            </div>

            <div className="wave-ctrl">
              <motion.button className="cbtn glass wave-no" aria-label="Не нравится"
                             whileTap={{ scale: .86 }}
                             onClick={dislike} disabled={!cur}>
                <WaveNoIcon />
              </motion.button>

              <WaveHeart id={cur.id} big session={sess?.session_id} />

              <motion.button className="cbtn glass wave-dl" aria-label="Скачать в чат"
                             whileTap={{ scale: .86 }}
                             onClick={download} disabled={dl === 'busy'}>
                <AnimatedIcon name={dl === 'busy' ? 'busy' : dl === 'done' ? 'done' : dl === 'err' ? 'err' : 'idle'}>
                  {dl === 'busy' ? <span className="minispinner" />
                    : dl === 'done' ? <IcoCheck width="1em" height="1em" />
                      : dl === 'err' ? <span className="wave-dl-err">!</span>
                        : <IcoDownload />}
                </AnimatedIcon>
              </motion.button>

              <motion.button className="cbtn big wave-next" aria-label="Другой трек"
                             whileTap={{ scale: .9 }}
                             onClick={() => { haptic('medium'); P.next(); }}>
                <IcoNext />
              </motion.button>

              <motion.button className="cbtn big" aria-label="Играть/пауза"
                             whileTap={{ scale: .9 }}
                             onClick={() => { haptic('medium'); P.toggle(); }}>
                <PlayPause playing={st.playing} loading={st.loading} big />
              </motion.button>
            </div>

            <WaveHistory queue={st.queue} index={st.index} />

            <div className="wave-foot">
              <span className={'wave-engine ' + (sess?.engine === 'yandex' ? 'ym' : 'own')}>
                {sess?.engine === 'yandex' ? '⚡ Волна ЯМ' : '◈ Свой движок'}
              </span>
              <AnimatePresence initial={false}>
                {st.loading && (
                  <motion.span key="load" className="minispinner"
                               initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} />
                )}
              </AnimatePresence>
            </div>
          </motion.div>
        ) : phase === 'err' ? (
          <motion.div key="wave-err" className="wave-stage"
                      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                      transition={{ duration: .24, ease: EASE }}>
            <div className="cardbox">
              <div className="login">
                <h2>Волна тихая…</h2>
                <p className="muted">{err}</p>
                <button className="bigbtn primary" onClick={() => startWave(ws?.engine)}>Ещё раз</button>
              </div>
            </div>
          </motion.div>
        ) : (
          <motion.div key="wave-boot" className="wave-stage"
                      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                      transition={{ duration: .2, ease: EASE }}>
            <div className="boot-eq wave-boot"><i /><i /><i /></div>
            <p className="muted wave-boottext">Настраиваю волну…</p>
          </motion.div>
        )}
      </AnimatePresence>

      <FormulaSheet open={sheet} ws={ws} dirty={dirty}
        onClose={() => setSheet(false)} onChange={patchFormula}
        onRestart={() => { setSheet(false); setDirty(false); startWave(ws?.engine); }} />
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
    <FadeImg className={'wave-cover' + (playing ? ' kb' : '')} src={src} alt=""
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
          <motion.button
            key={(t.id || 'x') + '-' + i}
            layout
            className="wave-hist-chip"
            title={t.title}
            aria-label={`Вернуться: ${t.artist} — ${t.title}`}
            initial={{ opacity: 0, scale: .78 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: .24, ease: EASE, delay: Math.min(i, 6) * .03 }}
            whileHover={{ scale: 1.07 }}
            whileTap={{ scale: .93 }}
            onClick={() => {
              haptic('soft');
              const qi = queue.findIndex(x => x.id === t.id);
              if (qi >= 0) P.play([...queue], qi);
            }}
          >
            {t.cover_url
              ? <FadeImg src={coverOr(t)} alt="" />
              : <span className="wave-hist-glyph">≋</span>}
          </motion.button>
        ))}
      </div>
    </div>
  );
}

function WaveHeart({ id, session, big }) {
  const liked = useLikes().has(id);
  const controls = useAnimationControls();
  return (
    <motion.button
      className={'heart' + (liked ? ' on' : '') + (big ? ' lg' : '')}
      aria-label="В любимое"
      whileTap={{ scale: .82 }}
      animate={controls}
      onClick={async (e) => {
        e.stopPropagation();
        haptic(liked ? 'soft' : 'light');
        const nowLiked = await toggleLike(id);
        if (session) waveFeedback(session, nowLiked ? 'like' : 'unlike', id).catch(() => {});
        if (nowLiked) controls.start({ scale: [1, 1.18, 1] }, { duration: .32, ease: [.2, .8, .2, 1] });
      }}
    >
      <svg viewBox="0 0 24 24"><path d={HEART_D} /></svg>
    </motion.button>
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

/* ---------------- формула bottom-sheet ---------------- */
function FormulaSheet({ open, ws, dirty, onClose, onChange, onRestart }) {
  const chipOn = (cond) => 'chip' + (cond ? ' on' : '');
  if (!ws) return null;

  return (
    <Sheet open={open && !!ws} onClose={onClose} labelledBy="wave-formula-title">
      <div className="wsheet-head">
        <div className="wsheet-title" id="wave-formula-title">Формула волны</div>
        <motion.button className="wsheet-x" aria-label="Закрыть формулу"
                       whileTap={{ scale: .88 }} onClick={onClose}>✕</motion.button>
      </div>

      {dirty && (
        <motion.button className="wave-dirty"
                       initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}
                       transition={{ duration: .2, ease: EASE }}
                       onClick={onRestart}>
          Формула обновилась — перезапусти волну ⟳
        </motion.button>
      )}

      <div className="wsec">
        <div className="wsec-t">Настроение</div>
        <div className="chiprow">
          {MOODS.map(m => (
            <motion.button key={String(m.id)} className={chipOn((ws.mood ?? null) === m.id)}
                           whileTap={{ scale: .95 }}
                           onClick={() => onChange({ mood: m.id })}
                           disabled={m.id === null && ws.mood === null}>
              {m.icon} {m.label}
            </motion.button>))}
        </div>
      </div>

      <div className="wsec">
        <div className="wsec-t">Разнообразие</div>
        <div className="chiprow">
          {DIVERSITY.map(v => (
            <motion.button key={v.id} className={chipOn((ws.diversity || 'default') === v.id)}
                           whileTap={{ scale: .95 }}
                           onClick={() => onChange({ diversity: v.id })}>
              {v.icon} {v.label}
            </motion.button>))}
        </div>
      </div>

      <div className="wsec">
        <div className="wsec-t">Занятие</div>
        <div className="chiprow">
          {ACTIVITIES.map(a => (
            <motion.button key={a.id} className={chipOn((ws.activities || []).includes(a.id))}
                           whileTap={{ scale: .95 }}
                           onClick={() => onChange({
                             activities: ((ws.activities || []).includes(a.id)
                               ? (ws.activities || []).filter(x => x !== a.id)
                               : [...(ws.activities || []), a.id]) })}>
              {a.icon} {a.label}
            </motion.button>))}
        </div>
      </div>

      <div className="wsec">
        <div className="wsec-t">Жанры</div>
        <div className="chiprow">
          {GENRES.map(g => (
            <motion.button key={g} className={chipOn((ws.genres || []).includes(g))}
                           whileTap={{ scale: .95 }}
                           onClick={() => onChange({
                             genres: ((ws.genres || []).includes(g)
                               ? (ws.genres || []).filter(x => x !== g)
                               : [...(ws.genres || []), g]) })}>
              {GENRE_LABEL[g]}
            </motion.button>))}
        </div>
      </div>

      <div className="wsec">
        <div className="wsec-t">Язык</div>
        <div className="chiprow">
          {LANGS.map(v => (
            <motion.button key={v.id} className={chipOn((ws.language || 'any') === v.id)}
                           whileTap={{ scale: .95 }}
                           onClick={() => onChange({ language: v.id })}>
              {v.icon} {v.label}
            </motion.button>))}
        </div>
      </div>

      <div className="wsec row3">
        <div>
          <div className="wsec-t">Треки 18+</div>
          <div className="chiprow">
            <motion.button className={chipOn(ws.allow_explicit)} whileTap={{ scale: .95 }}
                           onClick={() => onChange({ allow_explicit: true })}>Разрешено</motion.button>
            <motion.button className={chipOn(!ws.allow_explicit)} whileTap={{ scale: .95 }}
                           onClick={() => onChange({ allow_explicit: false })}>Без 18+</motion.button>
          </div>
        </div>
        <div>
          <div className="wsec-t">Движок</div>
          <div className="chiprow">
            {ENGINES.map(v => (
              <motion.button key={v.id} className={chipOn((ws.engine || 'auto') === v.id)}
                             title={v.hint} whileTap={{ scale: .95 }}
                             onClick={() => onChange({ engine: v.id })}>
                {v.label}
              </motion.button>))}
          </div>
        </div>
      </div>
    </Sheet>
  );
}
