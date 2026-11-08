/* Единая точка проверки reduced motion. MotionConfig reducedMotion="user"
   уже уважает настройку, но отдельным компонентам нужно знать её явно:
   например, чтобы не запускать бесконечный ken-burns и reveal-смещения. */
import { useReducedMotion as useMotionReducedMotion } from 'motion/react';

export function useReducedMotion() {
  return !!useMotionReducedMotion();
}

export default useReducedMotion;
