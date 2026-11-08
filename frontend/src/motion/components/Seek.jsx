/* Временная шкала, которая живёт от реального audio.currentTime.
   Внутренний requestAnimationFrame пишет в DOM (input.value, --fill, время),
   не вызывая React-перерендеров. Перемотка выполняет настоящий seek. */
import React, { useEffect, useRef } from 'react';
import * as P from '../../player.js';

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

function fmt(s) {
  s = Math.max(0, Math.round(s || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, '0')}`;
}

export default function Seek({
  className = 'seek', showTime = false, ariaLabel = 'Позиция трека',
  timeClass = 'ttime', wrapClass = '', ...rest
}) {
  const inputRef = useRef(null);
  const curRef = useRef(null);
  const durRef = useRef(null);
  const tipRef = useRef(null);
  const dragging = useRef(false);

  useEffect(() => {
    let raf = 0;
    let lastCur = -1;
    let lastDur = -1;

    const tick = () => {
      raf = requestAnimationFrame(tick);
      if (document.hidden || dragging.current) return;

      const s = P.getState();
      const dur = s.duration || 0;
      const cur = s.currentTime || 0;
      const pct = dur ? (cur / dur) * 100 : 0;
      const el = inputRef.current;

      if (el) {
        const v = String(Math.round(pct * 10));
        if (el.value !== v) el.value = v;
        el.style.setProperty('--fill', pct.toFixed(1) + '%');
      }
      if (curRef.current && Math.abs(cur - lastCur) >= 0.2) {
        curRef.current.textContent = fmt(cur);
        lastCur = cur;
      }
      if (durRef.current && dur !== lastDur) {
        durRef.current.textContent = fmt(dur);
        lastDur = dur;
      }
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  /* Отпускание указателя вне ползунка не должно оставить его «зажатым». */
  useEffect(() => {
    const up = () => { dragging.current = false; };
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
    return () => {
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
    };
  }, []);

  const onMove = e => {
    const el = inputRef.current;
    const tip = tipRef.current;
    if (!el || !tip) return;
    const r = el.getBoundingClientRect();
    if (!r.width) return;
    const ratio = clamp((e.clientX - r.left) / r.width, 0, 1);
    tip.textContent = fmt(ratio * (P.getState().duration || 0));
    tip.style.left = (ratio * 100) + '%';
    tip.style.opacity = '1';
  };
  const onLeave = () => {
    if (tipRef.current) tipRef.current.style.opacity = '0';
  };

  return (
    <>
      {showTime && <span className={timeClass} ref={curRef}>0:00</span>}
      <span
        className={'seekwrap ' + wrapClass}
        onPointerMove={onMove}
        onPointerLeave={onLeave}
      >
        <span className="seek-tip" ref={tipRef} aria-hidden="true">0:00</span>
        <input
          ref={inputRef}
          className={className}
          type="range"
          min="0"
          max="1000"
          step="1"
          defaultValue="0"
          aria-label={ariaLabel}
          onPointerDown={() => { dragging.current = true; }}
          onChange={e => P.seek(+e.target.value / 1000)}
          {...rest}
        />
      </span>
      {showTime && <span className={timeClass} ref={durRef}>0:00</span>}
    </>
  );
}
