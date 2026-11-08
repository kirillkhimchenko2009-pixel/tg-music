/* Переходы страниц: исчезновение 115 ms, появление 220 ms + 7 px.
   Саидбар, топбар, мини-плеер и таббар остаются неподвижными. */
import { durations } from '../config/durations.js';
import { easings } from '../config/easings.js';
import { page } from '../config/variants.js';

export const pageVariants = page;
export const pageTransition = { duration: durations.normal, ease: easings.enter };

export const pageEnter = {
  initial: { opacity: 0, y: 7 },
  animate: { opacity: 1, y: 0, transition: { duration: durations.normal, ease: easings.enter } },
  exit: { opacity: 0, y: -4, transition: { duration: 0.115, ease: easings.exit } },
};

/* Герой-секции внутри страницы: чуть длиннее, чем обычный список. */
export const heroEnter = {
  initial: { opacity: 0, y: 14 },
  animate: { opacity: 1, y: 0, transition: { duration: durations.medium, ease: easings.enter } },
  exit: { opacity: 0, transition: { duration: durations.fast, ease: easings.exit } },
};
