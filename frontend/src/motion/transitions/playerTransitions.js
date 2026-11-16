/* Плеер — центральный элемент. Обложка mini → fullscreen связана layoutId,
   поверхность раскрывается мягко, контент проявляется без transform,
   чтобы не сбить layout-анимацию общей обложки. */
import { durations } from '../config/durations.js';
import { easings } from '../config/easings.js';
import { springs } from '../config/springs.js';

export const player = {
  /* общая обложка: 450–650 ms, как в промте */
  sharedCover: { type: 'spring', stiffness: 180, damping: 34, mass: 1 },
  surface: { duration: durations.slow, ease: easings.emphasized },
  surfaceExit: { duration: durations.medium, ease: easings.exit },
  content: { duration: durations.medium, ease: easings.enter },
  track: { duration: durations.normal, ease: easings.standard },
  queue: springs.gentle,
};

/* Поверхность fullscreen: не двигаем сам контейнер с обложкой,
   раскрываем отдельный фоновый слой. */
export const playerSurfaceVariants = {
  hidden: { opacity: 0, scaleY: 0.965, scaleX: 0.985 },
  visible: {
    opacity: 1, scaleY: 1, scaleX: 1,
    transition: { duration: durations.slow, ease: easings.emphasized },
  },
  exit: { opacity: 0, scaleY: 0.975, transition: { duration: durations.medium, ease: easings.exit } },
};

export const playerContentVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { duration: durations.medium, ease: easings.enter, delay: 0.10 },
  },
  exit: { opacity: 0, transition: { duration: durations.fast, ease: easings.exit } },
};

export const trackMetaVariants = {
  hidden: { opacity: 0, y: 7 },
  visible: { opacity: 1, y: 0, transition: { duration: durations.normal, ease: easings.enter } },
  exit: { opacity: 0, y: -6, transition: { duration: durations.fast, ease: easings.exit } },
};

export const queueItemVariants = {
  hidden: { opacity: 0, x: -7 },
  visible: (i = 0) => ({
    opacity: 1, x: 0,
    transition: { duration: durations.normal, ease: easings.enter, delay: Math.min(i, 12) * 0.035 },
  }),
  exit: { opacity: 0, transition: { duration: durations.fast, ease: easings.exit } },
};
