/* Набор параметров плеера с учётом reduced motion. */
import { useReducedMotion } from './useReducedMotion.js';
import {
  player, playerContentVariants, playerSurfaceVariants, trackMetaVariants, queueItemVariants,
} from '../transitions/playerTransitions.js';

export function usePlayerMotion() {
  const reduced = useReducedMotion();
  return {
    reduced,
    player,
    surfaceVariants: playerSurfaceVariants,
    contentVariants: playerContentVariants,
    trackVariants: trackMetaVariants,
    queueVariants: queueItemVariants,
    sharedCover: reduced
      ? { duration: 0.12, ease: 'easeOut' }
      : player.sharedCover,
  };
}

export default usePlayerMotion;
