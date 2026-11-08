/* Вступительная анимация страницы проигрывается один раз за сессию.
   На повторном заходе контент сразу виден — остаётся только мягкий
   page-transition. ready нужен экранам, которые сначала показывают скелетон. */
import { useEffect, useState } from 'react';

const visited = new Set();

export function useEnterOnce(scope, ready = true) {
  const [first] = useState(() => !visited.has(scope));
  useEffect(() => {
    if (first && ready) visited.add(scope);
  }, [first, ready, scope]);
  return first;
}

export function markVisited(scope) {
  visited.add(scope);
}

export function resetVisited(scope) {
  if (scope == null) visited.clear();
  else visited.delete(scope);
}

export default useEnterOnce;
