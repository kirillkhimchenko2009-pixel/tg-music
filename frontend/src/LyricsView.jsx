/* LYRICS VIEW §11: пользовательский LRC/текст — импорт (.lrc/.txt или
   вставка), локальное хранение (track_lyrics), синхронная подсветка
   строки, тап → seek, автоследование с паузой при ручном скролле,
   переключение «Синхронно / обычный текст», удаление.
   Тексты НЕ качаются извне: права на слова принадлежат авторам —
   пользователь добавляет только свои легальные копии. */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { haptic } from './tg.js';
import { toast } from './motion/utils/toast.js';
import { IcoCheck, IcoX } from './ui.jsx';
import * as P from './player.js';
import * as L from './local/lyrics.js';

export function LyricsView({ track, st }) {
  const [lyr, setLyr] = useState(null);          // {mode, lines, raw}
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [plain, setPlain] = useState(false);     // «обычный текст» вместо синхронного
  const [follow, setFollow] = useState(true);
  const fileRef = useRef(null);
  const boxRef = useRef(null);
  const holdTimer = useRef(null);
  const id = track?.id;

  useEffect(() => {
    let dead = false;
    setLyr(null); setPlain(false); setFollow(true);
    L.getLyrics(id).then(l => { if (!dead && l) setLyr(l); });
    return () => { dead = true; };
  }, [id]);

  const active = useMemo(() => {
    if (!lyr || lyr.mode !== 'lrc' || plain) return -1;
    return L.currentLine(lyr.lines, st.currentTime || 0);
  }, [lyr, plain, st.currentTime]);

  /* автоследование §11: активная строка в центре; ручной скролл — пауза 5 с */
  useEffect(() => {
    if (active < 0 || !follow) return;
    const el = boxRef.current?.querySelector(`[data-i="${active}"]`);
    if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [active, follow, lyr]);

  const manualScroll = () => {
    setFollow(false);
    if (holdTimer.current) clearTimeout(holdTimer.current);
    holdTimer.current = setTimeout(() => setFollow(true), 5000);
  };

  const save = async (text, what) => {
    try {
      const p = await L.saveLyrics(id, text);
      setLyr({ ...p, raw: text });
      setEditing(false);
      haptic('ok');
      toast(what || (p.mode === 'lrc' ? 'Синхронный текст сохранён на устройстве'
                                       : 'Текст сохранён на устройстве'),
            { kind: 'success' });
    } catch (e) {
      toast((e && e.message) || 'Не удалось сохранить', { kind: 'error' });
    }
  };

  const onFile = e => {
    const f = e.target.files?.[0];
    e.target.value = '';
    if (!f || !id) return;
    const r = new FileReader();
    r.onload = () => save(String(r.result || ''), `Импортировано: ${f.name}`);
    r.onerror = () => toast('Файл не читается', { kind: 'error' });
    r.readAsText(f);
  };

  if (!id) return null;
  const hasLrc = !!lyr && lyr.mode === 'lrc' && !plain;

  return (
    <div className="lyr">
      <div className="lyr-head">
        <span className="lyr-cap">Текст</span>
        <span className="lyr-acts">
          {lyr?.mode === 'lrc' && (
            <button className="lyr-act"
                    onClick={() => { haptic('light'); setPlain(p => !p); }}>
              {plain ? 'Синхронно' : 'Обычный текст'}
            </button>
          )}
          <button className="lyr-act"
                  onClick={() => { haptic('light'); setDraft(lyr?.raw || ''); setEditing(v => !v); }}>
            {lyr ? 'Заменить' : 'Вставить'}
          </button>
          <button className="lyr-act" onClick={() => { haptic('light'); fileRef.current?.click(); }}>
            Файл .lrc
          </button>
          {lyr && (
            <button className="lyr-act" aria-label="Удалить текст"
                    onClick={() => {
                      if (!confirm('Удалить текст с устройства?')) return;
                      haptic('ok');
                      L.removeLyrics(id);
                      setLyr(null);
                    }}>
              <IcoX />
            </button>
          )}
        </span>
        <input ref={fileRef} type="file" accept=".lrc,.txt,text/plain" hidden onChange={onFile} />
      </div>

      {editing && (
        <div className="lyr-edit">
          <textarea value={draft} onChange={e => setDraft(e.target.value)}
                    placeholder={'[00:12.50] первая строка…\nили просто текст без таймингов'}
                    rows={6} />
          <div className="lyr-editrow">
            <button className="lyr-save" onClick={() => save(draft)} disabled={!draft.trim()}>
              <IcoCheck /> Сохранить на устройстве
            </button>
            <button className="lyr-act" onClick={() => setEditing(false)}>Отмена</button>
          </div>
          <div className="lyr-note">
            Тексты — только твои: Vibe не качает слова песен сам, права принадлежат
            авторам. Импортированный .lrc живёт локально и играет офлайн вместе
            с сохранённым треком.
          </div>
        </div>
      )}

      {!lyr && !editing && (
        <div className="lyr-empty">Текста пока нет — вставь его или импортируй .lrc.
          Сохранится на устройстве и будет доступен офлайн.</div>
      )}

      {lyr && !editing && (
        <div className="lyr-box" ref={boxRef}
             onTouchMove={manualScroll} onWheel={manualScroll}>
          {lyr.lines.map((l, i) => (
            <button key={i} data-i={i}
                    className={'lyr-line' + (hasLrc && i === active ? ' on' : '')}
                    onClick={() => {
                      if (hasLrc && l.t != null) {
                        haptic('light');
                        const d = st.duration || track.duration || 0;
                        if (d) P.seek(l.t / d);
                      }
                    }}>
              {l.text}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
