/* Параметры page-transition и память о посещённых ключах. */
import { useEffect, useRef } from 'react';
import { pageVariants, pageTransition } from '../transitions/pageTransitions.js';

const seen = new Set();

export function usePageTransition(key) {
  const first = useRef(!seen.has(key));
  useEffect(() => {
    seen.add(key);
    first.current = false;
  }, [key]);
  return {
    first: first.current,
    variants: pageVariants,
    transition: pageTransition,
  };
}

export default usePageTransition;
