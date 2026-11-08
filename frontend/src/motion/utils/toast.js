/* Лёгкая шина уведомлений: без контекста и лишних ререндеров дерева. */
let items = [];
let seq = 0;
const listeners = new Set();
const timers = new Map();

const emit = () => {
  items = [...items];
  for (const fn of listeners) fn();
};

export function toast(message, opts = {}) {
  const id = ++seq;
  const item = {
    id,
    message,
    kind: opts.kind || 'info',      // info | success | error
    icon: opts.icon || null,
    duration: opts.duration ?? 3200,
  };
  items = [...items, item].slice(-4);
  emit();
  if (item.duration > 0) {
    const t = setTimeout(() => dismissToast(id), item.duration);
    timers.set(id, t);
  }
  return id;
}

export function dismissToast(id) {
  const t = timers.get(id);
  if (t) { clearTimeout(t); timers.delete(id); }
  if (!items.some(x => x.id === id)) return;
  items = items.filter(x => x.id !== id);
  emit();
}

export function subscribeToasts(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getToasts() { return items; }

export default toast;
