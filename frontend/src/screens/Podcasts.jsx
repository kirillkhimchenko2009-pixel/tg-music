/* Подкасты: раздел демо — ежевика-звон подкаст-платформы в Vibe ещё в работе.
   Честно помечено; кнопки воспроизведения отсутствуют (никаких фейков). */
import React from 'react';
import { gradFor, IcoMic } from '../ui.jsx';
import { haptic } from '../tg.js';

const DEMO = [
  { t: 'Дизайн слышимого', s: 'О том, как звучат интерфейсы', w: 34 },
  { t: 'Кабинет продюсера', s: 'Разговоры о жизни музыки', w: 58 },
  { t: 'Ночные эфиры', s: 'Лоу-фай и шум дождя', w: 47 },
  { t: 'Техно-лаб', s: 'Синтезаторы и нейрофреймы', w: 26 },
  { t: 'Первый дубль', s: 'Подкаст для киныманов', w: 41 },
  { t: 'Соло-путь', s: 'Истории независимых артистов', w: 39 },
];

export default function Podcasts() {
  return (
    <div>
      <h1 className="h">Подкасты</h1>
      <div className="sub">
        Раздел уже готовит премьеру: страницы подкастов, эпизоды и плеер эпизодов.
        Пока доступны превью карточек — {DEMO.length} демо-рубрики.
      </div>

      <div className="empty" style={{ padding: '22px 0', textAlign: 'left', display: 'block' }}>
        <div className="cap" style={{ marginTop: 0 }}>Демо-каталог · воспроизведение скоро</div>
        <div className="albgrid">
          {DEMO.map((p, i) => (
            <div className="card rise" key={p.t} style={{ animationDelay: i * 40 + 'ms' }}>
              <div className="cardcov" style={{ cursor: 'default' }}>
                <span className="cph" style={{ background: gradFor(p.t) }}>
                  <IcoMic width="1em" height="1em" style={{ width: '28%', height: '28%' }} />
                </span>
                <span className="badge">Демо · ~{p.w} мин</span>
              </div>
              <span className="cardname">{p.t}</span>
              <span className="cardsub">{p.s}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="cardbox" style={{ marginTop: 8 }}>
        <div style={{ fontWeight: 700, fontFamily: 'var(--font-display)' }}>Что появится</div>
        <div className="muted" style={{ marginTop: 6, fontSize: 13, lineHeight: 1.6 }}>
          · каталог подкастов и эпизодов · подписки и новинки · умная скорость
          воспроизведения · загрузка эпизодов в чат.
        </div>
      </div>
    </div>
  );
}
