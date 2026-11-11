/* «Встряхнуть волну» — DeviceMotion: физическая встряска телефона
   запускает то же действие, что кнопка ⚡ в интерфейсе волны.

   Правила (§8): меняет сценарий, а не вкус; всегда есть кнопка-фолбэк
   (motion может быть недоступен — ноутбук, десктоп, отказ в разрешении).

   iOS ≥ 13 требует явного запроса разрешения (DeviceMotionEvent.requestPermission),
   поэтому enable() — async и зовётся из пользовательского жеста (клик/тап).
   Защита от дребезга: порог |a| − g > THRESHOLD и cooldown между срабатываниями.
*/
import { haptic } from './tg.js';

const THRESHOLD = 13.5;      // м/с² отклонения от g — заметная встряска
const COOLDOWN = 1600;       // мс — не чаще раза в 1.6 с

let listening = false;
let handler = null;
let lastFire = 0;
let suppressed = false;      // сервер уже кулдаунит; не дёргаем лишний раз

export function supported() {
  return typeof window !== 'undefined'
    && typeof window.DeviceMotionEvent !== 'undefined';
}

export async function enable(onShake) {
  if (!supported() || listening || typeof onShake !== 'function') return false;
  handler = (e) => {
    if (suppressed) return;
    const a = e.accelerationIncludingGravity;
    if (!a || (a.x == null && a.y == null && a.z == null)) return;
    const mag = Math.sqrt((a.x || 0) ** 2 + (a.y || 0) ** 2 + (a.z || 0) ** 2);
    const now = Date.now();
    if (Math.abs(mag - 9.81) > THRESHOLD && now - lastFire > COOLDOWN) {
      lastFire = now;
      haptic('heavy');
      try { onShake(); } catch { /* UI-хук не должен ронять листенер */ }
    }
  };
  try {
    if (typeof DeviceMotionEvent.requestPermission === 'function') {
      const res = await DeviceMotionEvent.requestPermission();
      if (res !== 'granted') return false;
    }
    window.addEventListener('devicemotion', handler);
    listening = true;
    return true;
  } catch {
    return false;             // нет поддержки/разрешения — остаётся кнопка
  }
}

export function disable() {
  if (!listening) return;
  window.removeEventListener('devicemotion', handler);
  listening = false;
  handler = null;
}

/* Пока запрос к серверу в полёте — гасим повторные физические встряски
   (у кнопки свой busy-статус, у motion — этот). */
export function setSuppressed(v) { suppressed = !!v; }
