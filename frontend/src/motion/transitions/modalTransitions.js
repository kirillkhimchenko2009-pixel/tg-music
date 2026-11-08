/* Модальные окна, шиты и поповеры. */
import { durations } from '../config/durations.js';
import { easings } from '../config/easings.js';
import { springs } from '../config/springs.js';

export const overlayVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: durations.normal, ease: easings.standard } },
  exit: { opacity: 0, transition: { duration: durations.fast, ease: easings.exit } },
};

export const sheetVariants = {
  hidden: { y: '102%' },
  visible: { y: 0, transition: springs.gentle },
  exit: { y: '102%', transition: { duration: durations.medium, ease: easings.exit } },
};

export const modalVariants = {
  hidden: { opacity: 0, scale: 0.97, y: 8 },
  visible: {
    opacity: 1, scale: 1, y: 0,
    transition: { duration: durations.medium, ease: easings.emphasized },
  },
  exit: {
    opacity: 0, scale: 0.98, y: 4,
    transition: { duration: durations.normal, ease: easings.exit },
  },
};

export const popVariants = {
  hidden: { opacity: 0, y: -6, scale: 0.985 },
  visible: { opacity: 1, y: 0, scale: 1, transition: { duration: durations.normal, ease: easings.enter } },
  exit: { opacity: 0, y: -4, scale: 0.99, transition: { duration: durations.fast, ease: easings.exit } },
};

export const popTransition = { duration: durations.normal, ease: easings.enter };
