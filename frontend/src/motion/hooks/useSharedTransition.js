/* layoutId-связка обложек: мини-плеер ↔ фулл-скрин. */
import { useReducedMotion } from './useReducedMotion.js';
import { player } from '../transitions/playerTransitions.js';

export function useSharedTransition(id = 'np-cover') {
  const reduced = useReducedMotion();
  return {
    layoutId: id,
    transition: reduced ? { duration: 0.12, ease: 'easeOut' } : player.sharedCover,
  };
}

export default useSharedTransition;
