/* Аккуратное завершение анимаций при размонтировании/смене контента,
   чтобы быстрые переключения не накапливали конкурирующие твины. */
export function stopAnimations(el) {
  if (!el || typeof el.getAnimations !== 'function') return;
  try {
    for (const a of el.getAnimations()) a.cancel();
  } catch { /* noop */ }
}

export function releaseWillChange(el) {
  if (!el) return;
  el.style.willChange = 'auto';
}

export function cleanupMotion(el) {
  stopAnimations(el);
  releaseWillChange(el);
}

export default cleanupMotion;
