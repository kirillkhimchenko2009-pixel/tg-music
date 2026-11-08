/* Появление секции при первом попадании в вьюпорт.
   Observer отключается после срабатывания — карточки не перезапускают
   анимацию при каждом движении скролла. */
import { useEffect, useRef, useState } from 'react';

export function useSectionReveal({ threshold = 0.12, rootMargin = '0px 0px -7% 0px' } = {}) {
  const ref = useRef(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    let reduced = false;
    try { reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { /* noop */ }
    if (reduced || typeof IntersectionObserver === 'undefined') {
      setShown(true);
      el.classList.add('in');
      return;
    }

    const io = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) {
        setShown(true);
        el.classList.add('in');
        io.disconnect();
      }
    }, { threshold, rootMargin });

    io.observe(el);
    return () => io.disconnect();
  }, [threshold, rootMargin]);

  return { ref, shown };
}

export default useSectionReveal;
