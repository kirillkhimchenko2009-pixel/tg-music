/* Навигация приложения: стек «вью» ({tab, sub, ...}) + кнопки назад/вперёд.
   Тонкий router без react-router: Telegram-вебвью живёт в одном документе,
   история нужна домашнему топбару и внутренней навигации экранов. */
import { useSyncExternalStore } from 'react';

const MAX = 40;
let stack = [];
let ptr = -1;
const listeners = new Set();
const DEFAULT_VIEW = { tab: 'home', sub: null };

const emit = () => {
  meta = { canBack: ptr > 0, canFwd: ptr < stack.length - 1 };
  for (const fn of listeners) fn();
};

const keyOf = v => JSON.stringify(v && (v.tab ?? v.id) !== undefined
  ? { tab: v.tab, sub: v.sub, id: v.id } : v ?? null);

let meta = { canBack: false, canFwd: false };

export function navInit(v) {
  if (ptr >= 0) return;
  stack = [v]; ptr = 0;
  emit();
}

export function navGet() { return stack[ptr] || DEFAULT_VIEW; }
export function navBack() {
  if (ptr > 0) { ptr -= 1; emit(); return true; }
  return false;
}
export function navFwd() {
  if (ptr < stack.length - 1) { ptr += 1; emit(); }
}
export function navCanBack() { return ptr > 0; }
export function navCanFwd() { return ptr < stack.length - 1; }

export function navPush(v) {
  if (keyOf(v) === keyOf(stack[ptr])) return;
  // заменяем впереди стоящие вью (как браузер) + ограничиваем глубину
  stack = stack.slice(0, ptr + 1);
  stack.push(v);
  if (stack.length > MAX) stack = stack.slice(-MAX);
  ptr = stack.length - 1;
  emit();
}

export function navReplace(v) {
  if (!stack.length) { navInit(v); return; }
  stack[ptr] = v;
  emit();
}

function sub(fn) { listeners.add(fn); return () => listeners.delete(fn); }

export function useNav() {
  return useSyncExternalStore(sub, navGet, navGet);
}

/* снапшот доступности кнопок назад/вперёд (для топбара) */
export function useNavMeta() {
  return useSyncExternalStore(sub, () => meta, () => meta);
}
