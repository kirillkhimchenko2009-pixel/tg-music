/* Волна — аналог «Мои волны» Яндекса: личный бесконечный поток.
   Гибрид: движок A — rotor ЯМ (у владельцев /token), движок B — наш подбор
   (services/wave/intel: вкус, контекст, сценарии с честными названиями).
   Карточки сценариев + встряска (кнопка ⚡ и DeviceMotion) + ряд открытий.
   Формула в bottom-sheet: настроение, занятие, жанры, знакомство, язык, 18+. */
import React, { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useAnimationControls } from 'motion/react';
import {
  api, waveStart, waveNext, waveFeedback, waveSettings, waveSaveSettings,
  waveEvent, waveScenarios, waveShake, waveDiscovery, waveCtx,
} from '../api.js';
import * as P from '../player.js';
import { usePlayerSnap } from '../player.js';
import * as shake from '../shake.js';
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
const FAMILIARITY = [
  { id: null, icon: '∞', label: 'Как есть' },
  { id: 'favorite', icon: '❤', label: 'Любимое' },
  { id: 'similar', icon: '≈', label: 'Похожее' },
  { id: 'new', icon: '✦', label: 'Новое' },
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

/* палитра сценария → rgba для ауры и рамок (без color-mix: webview) */
function hexA(hex, a) {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || '');
  if (!m) return null;
  return `rgba(${parseInt(m[1], 16)},${parseInt(m[2], 16)},${parseInt(m[3], 16)},${a})`;
}

export default function Wave() {
  const st = usePlayerSnap();
  const [phase, setPhase] = useState('boot');   // boot | ready | err
  const [err, setErr] = useState('');
  const [ws, setWs] = useState(null);           // формула (settings DTO)
  const [sess, setSess] = useState(null);       // { session_id, engine }
  const [sheet, setSheet] = useState(false);
  const [dirty, setDirty] = useState(false);    // формула изменилась — жмёт перезапуск
  const [scens, setScens] = useState([]);       // карточки сценариев
  const [scen, setScen] = useState(null);       // активный сценарий (DTO)
  const [disc, setDisc] = useState([]);         // карточки открытий
  const [fam, setFam] = useState(null);         // явная степень знакомства
  const [shakeBusy, setShakeBusy] = useState(false);
  const [autoPend, setAutoPend] = useState(false); // настройка меняется — волна сейчас перезапустится сама
  const booted = useRef(false);
  const scenRef = useRef(null);                 // для fetchMore без устаревания
  const famRef = useRef(null);
  const restartTimer = useRef(null);            // дебаунс автообновления формулы
  const engineRef = useRef('auto');
  engineRef.current = ws?.engine || 'auto';

  const setScenBoth = (s) => { scenRef.current = s; setScen(s); };
  const setFamBoth = (v) => { famRef.current = v; setFam(v); };

  const startWave = async (engine, scenArg) => {
    const s = scenArg ?? scenRef.current;
    setPhase('boot'); setErr('');
    try {
      const j = await waveStart(engine || ws?.engine || 'auto', waveCtx({
        scenario: s?.id || null,
        familiarity: famRef.current || undefined,
      }));
      setSess({ session_id: j.session_id, engine: j.engine });
      if (j.scenario && (j.scenario.id || j.scenario.title)) setScenBoth(j.scenario);
      // порядок важен: play() сбрасывает wave-источник — волну включаем ПОСЛЕ
      await P.play(j.tracks || [], 0);
      P.setWaveSource({
        sessionId: j.session_id,
        fetchMore: async (last) => {
          const r = await waveNext(j.session_id, last, waveCtx({
            scenario: scenRef.current?.id || null,
            familiarity: famRef.current || undefined,
          }));
          return r.tracks || [];
        },
        onEvent: onWaveEvent(j.session_id),
      });
      setPhase('ready');
    } catch (e) {
      setErr((e && e.message) || 'волна не подала признаков жизни');
      setPhase('err'); haptic('heavy');
    }
  };

  /* события плеера → API. started/skip/finished идут в /feedback
     (сервер сам пишет WaveEvents, дублей нет); метки и seek — в /event. */
  const onWaveEvent = (sid) => (kind, track, played) => {
    if (!track) return;
    if (kind === 'started') {
      waveFeedback(sid, 'trackStarted', track.id).catch(() => {});
    } else if (kind === 'skip') {
      waveFeedback(sid, 'skip', track.id, played).catch(() => {});
    } else if (kind === 'finished') {
      waveFeedback(sid, 'trackFinished', track.id, played).catch(() => {});
    } else {
      waveEvent(sid, kind, { trackId: track.id, playedSeconds: played });
    }
  };

  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    (async () => {
      let formula = null;
      try { formula = await waveSettings(); setWs(formula); } catch { /* формула по умолчанию */ }
      waveScenarios(waveCtx()).then(j => setScens(j.scenarios || [])).catch(() => {});
      waveDiscovery(10).then(j => setDisc(j.artists || [])).catch(() => {});
      const snap = P.getState();
      if (P.inWave() && snap.queue.length) { setPhase('ready'); return; }
      if (!snap.playing) { await startWave(formula?.engine || 'auto'); }
      else setPhase('ready');   // играет обычная очередь — не перехватываем
    })();
  }, []);

  /* встряска: кнопка и физическая — одно действие, сервер кулдаунит */
  const doShake = async () => {
    if (!sess || shakeBusy) return;
    haptic('heavy');
    setShakeBusy(true);
    shake.setSuppressed(true);
    try {
      const r = await waveShake(sess.session_id);
      if (r.scenario) setScenBoth(r.scenario);
      P.replaceUpcoming(r.tracks || []);
      toast(r.note || 'Волна встряхнута', { kind: 'success' });
    } catch (e) {
      toast((e && e.message) || 'волна ещё качается', { kind: 'info' });
    } finally {
      setShakeBusy(false);
      shake.setSuppressed(false);
    }
  };
  const doShakeRef = useRef(doShake);
  doShakeRef.current = doShake;
  useEffect(() => {
    shake.enable(() => doShakeRef.current());
    return () => shake.disable();
  }, []);

  const pickScenario = (s) => {
    if (!s) return;
    haptic('medium');
    setScenBoth(s);
    startWave(ws?.engine, s);
  };

  /* открытие из ряда discovery: лог события + волна открытий */
  const openDiscovery = (c) => {
    if (!c) return;
    haptic('soft');
    api('/api/wave/artist', { method: 'POST',
      body: JSON.stringify({ name: c.name }) }).catch(() => {});
    const dc = scens.find(s => s.id === 'discovery')
      || { id: 'discovery', title: 'Открытия',
           description: 'новые имена рядом с твоим вкусом', palette: 'discovery' };
    pickScenario(dc);
  };

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
    } catch (e) { fin = e.message === 'telegram_required' ? 'login' : 'err'; }

    if (fin === 'done') toast('Файл отправлен в чат', { kind: 'success' });
    else if (fin === 'err') toast('Не удалось скачать трек', { kind: 'error' });
    else if (fin !== 'login') toast('Файл ещё готовится — придёт в чат', { kind: 'info' });

    setDl(fin === 'done' ? 'done' : fin === 'err' ? 'err' : '');
    setTimeout(() => setDl(''), 4000);
  };

  /* автообновление: настройка сохранена → волна перезапускается сама.
     Дебаунс 1.4 с после ПОСЛЕДНЕГО изменения: тапы по чипам не спамят
     волнами; закрытие шита до таймера применяет изменения сразу. */
  const clearRestart = () => {
    if (restartTimer.current) { clearTimeout(restartTimer.current); restartTimer.current = null; }
  };
  const flushRestart = () => {
    clearRestart();
    setAutoPend(false);
    setSheet(false);
    setDirty(false);
    startWave(engineRef.current);
  };
  const scheduleRestart = (delay = 1400) => {
    clearRestart();
    setDirty(true);
    setAutoPend(true);
    restartTimer.current = setTimeout(() => {
      restartTimer.current = null;
      setAutoPend(false);
      setSheet(false);
      setDirty(false);
      startWave(engineRef.current);
    }, delay);
  };
  useEffect(() => () => clearRestart(), []);

  const patchFormula = async (patch) => {
    haptic('select');
    try {
      const j = await waveSaveSettings(patch);
      setWs(j);                       // шит оставляем открытым: можно донастроить
      scheduleRestart();              // волна обновится сама после последнего тапа
    } catch { /* тихая неудача настройки */ }
  };

  /* палитра активного сценария → аура и рамка активной карточки */
  const auraStyle = scen?.colors ? (() => {
    const acc = hexA(scen.colors.accent, .12) || 'rgba(217,237,58,.07)';
    const gl = hexA(scen.colors.glow, .12) || 'rgba(90,120,255,.06)';
    return { background:
      `radial-gradient(60% 50% at 70% 0%, ${acc}, transparent 60%),`
      + `radial-gradient(70% 60% at 20% 90%, ${gl}, transparent 60%)` };
  })() : undefined;

  return (
    <div className="waves"
         style={scen?.colors ? {
           '--wacc': hexA(scen.colors.accent, .5) || undefined,
           '--wgl': hexA(scen.colors.glow, .38) || undefined,
         } : undefined}>
      <div className="wave-aura" style={auraStyle} aria-hidden="true" />

      <div className="wave-head">
        <motion.div className="wave-title"
                   initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                   transition={{ duration: .28, ease: EASE }}>Волна</motion.div>
        <div className="wave-head-r">
          <motion.button className={'wave-shake' + (shakeBusy ? ' busy' : '')}
                         aria-label="Встряхнуть волну" title="Встряхнуть волну"
                         whileTap={{ scale: .88 }}
                         initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                         transition={{ duration: .28, ease: EASE, delay: .02 }}
                         onClick={doShake} disabled={shakeBusy || !sess}>
            <ShakeIcon />
          </motion.button>
          <motion.button className={'wave-formula' + (dirty ? ' dirty' : '')}
                         whileTap={{ scale: .965 }}
                         initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                         transition={{ duration: .28, ease: EASE, delay: .04 }}
                         onClick={() => { haptic('soft'); setSheet(true); }}>
            {formulaText(ws)}<span className="wave-gear">⛭</span>
          </motion.button>
        </div>
      </div>

      {scens.length > 0 && (
        <div className="wave-scens" role="tablist" aria-label="Сценарии волны">
          {scens.map((s, i) => (
            <motion.button
              key={s.id}
              className={'wave-scen' + ((scen?.id || 'foryou') === s.id ? ' on' : '')}
              style={(scen?.id || 'foryou') === s.id && s.colors
                ? {
                    borderColor: hexA(s.colors.accent, .65) || undefined,
                    background: hexA(s.colors.accent, .09) || undefined,
                  }
                : undefined}
              whileTap={{ scale: .96 }}
              initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              transition={{ duration: .26, ease: EASE, delay: Math.min(i, 5) * .035 }}
              onClick={() => (scen?.id || 'foryou') === s.id
                ? undefined : pickScenario(s)}
            >
              <span className="wave-scen-t">{s.title}</span>
              <span className="wave-scen-d">{s.description}</span>
            </motion.button>
          ))}
        </div>
      )}

      <AnimatePresence mode="wait" initial={false}>
        {phase === 'ready' && cur ? (
          <motion.div key="wave-ready" className="wave-stage"
                      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                      transition={{ duration: .26, ease: EASE }}>
            <div className="wave-covwrap">
              <AnimatePresence mode="popLayout" initial={false}>
                <motion.div
                  key={cur.id}
                  className="wave-covin"
                  initial={{ opacity: 0, scale: .93, rotate: -1.1 }}
                  animate={{ opacity: 1, scale: 1, rotate: 0 }}
                  exit={{ opacity: 0, scale: 1.04, rotate: 1.1 }}
                  transition={{ duration: .42, ease: EASE }}
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
                  transition={{ duration: .26, ease: EASE }}
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

            {disc.length > 0 && (
              <div className="wave-hist">
                <div className="wave-hist-t">Открытия рядом с тобой</div>
                <div className="wave-hist-row">
                  {disc.map((c, i) => (
                    <motion.button
                      key={c.artist_key}
                      className="wave-hist-chip"
                      title={c.name}
                      aria-label={`Открыть артиста: ${c.name}`}
                      initial={{ opacity: 0, scale: .78 }}
                      animate={{ opacity: 1, scale: 1 }}
                      transition={{ duration: .24, ease: EASE, delay: Math.min(i, 6) * .03 }}
                      whileHover={{ scale: 1.07 }}
                      whileTap={{ scale: .93 }}
                      onClick={() => openDiscovery(c)}
                    >
                      {c.cover_url
                        ? <FadeImg src={coverOr(c)} alt="" />
                        : <span className="wave-hist-glyph">♪</span>}
                    </motion.button>
                  ))}
                </div>
              </div>
            )}

            <div className="wave-foot">
              <span className={'wave-engine ' + (sess?.engine === 'yandex' ? 'ym' : 'own')}>
                {sess?.engine === 'yandex' ? '⚡ Волна ЯМ' : '◈ Свой движок'}
              </span>
              {scen?.title && (
                <span className="wave-scen-now">{scen.title}</span>
              )}
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

      <FormulaSheet open={sheet} ws={ws} dirty={dirty} fam={fam} autoPend={autoPend}
        onClose={() => { setSheet(false); if (restartTimer.current) flushRestart(); }}
        onChange={patchFormula}
        onFam={(v) => { haptic('select'); setFamBoth(v); scheduleRestart(); }}
        onRestart={() => { clearRestart(); setSheet(false); setDirty(false); setAutoPend(false); startWave(ws?.engine); }} />
    </div>
  );
}

function ShakeIcon() {
  return (
    <svg viewBox="0 0 24 24" className="ico" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="4" width="6" height="16" rx="2" />
      <path d="M4.5 9v6M19.5 9v6" />
    </svg>
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
function FormulaSheet({ open, ws, dirty, fam, autoPend, onClose, onChange, onFam, onRestart }) {
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
          {autoPend
            ? <><span className="minispinner" /> Обновляю волну…</>
            : 'Формула обновилась — перезапусти волну ⟳'}
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
        <div className="wsec-t">Знакомство</div>
        <div className="chiprow">
          {FAMILIARITY.map(f => (
            <motion.button key={String(f.id)} className={chipOn((fam ?? null) === f.id)}
                           whileTap={{ scale: .95 }}
                           onClick={() => onFam(f.id)}
                           disabled={f.id === null && (fam ?? null) === null}>
              {f.icon} {f.label}
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

      <div className="wsec">
        <div className="wsec-t">Контекст</div>
        <div className="wsec-hint">
          время суток, занятие и устройство помогают волне — без геолокации
        </div>
        <div className="chiprow">
          <motion.button className={chipOn(ws.context_enabled !== false)}
                         whileTap={{ scale: .95 }}
                         onClick={() => onChange({ context_enabled: true })}>Учитывать</motion.button>
          <motion.button className={chipOn(ws.context_enabled === false)}
                         whileTap={{ scale: .95 }}
                         onClick={() => onChange({ context_enabled: false })}>Отключить</motion.button>
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
