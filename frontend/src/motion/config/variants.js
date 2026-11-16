/* Общие варианты Motion. Все экраны используют их через transitions/*,
   чтобы характер движения менялся в одном месте. */
import { durations } from './durations.js';
import { easings } from './easings.js';

export const fade = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: durations.normal, ease: easings.standard } },
  exit: { opacity: 0, transition: { duration: durations.fast, ease: easings.exit } },
};

export const fadeUp = (distance = 8, delay = 0) => ({
  hidden: { opacity: 0, y: distance },
  visible: {
    opacity: 1, y: 0,
    transition: { duration: durations.medium, ease: easings.enter, delay },
  },
  exit: { opacity: 0, y: -Math.min(distance, 6), transition: { duration: durations.fast, ease: easings.exit } },
});

export const scaleIn = (delay = 0) => ({
  hidden: { opacity: 0, scale: 0.97 },
  visible: {
    opacity: 1, scale: 1,
    transition: { duration: durations.medium, ease: easings.emphasized, delay },
  },
  exit: { opacity: 0, scale: 0.985, transition: { duration: durations.fast, ease: easings.exit } },
});

/* Контейнер-стагер: дети проявляются волной, но не более 6–8 шагов.
   Стаггер чуть свободнее — «волна» читается глазом, а не мельтешит. */
export const staggerContainer = (stagger = 0.05, delayChildren = 0) => ({
  hidden: {},
  visible: {
    transition: { staggerChildren: stagger, delayChildren },
  },
});

export const staggerItem = {
  hidden: { opacity: 0, y: 14 },
  visible: {
    opacity: 1, y: 0,
    transition: { duration: durations.medium, ease: easings.enter },
  },
};

/* Карточка: вход мягкий, наведение/нажатие — в cardTransitions. */
export const cardRise = {
  hidden: { opacity: 0, y: 16, scale: 0.985 },
  visible: (delay = 0) => ({
    opacity: 1, y: 0, scale: 1,
    transition: { duration: durations.medium, ease: easings.enter, delay },
  }),
  exit: { opacity: 0, scale: 0.985, transition: { duration: durations.fast, ease: easings.exit } },
};

export const listItem = {
  hidden: { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0, transition: { duration: durations.normal, ease: easings.enter } },
  exit: { opacity: 0, scale: 0.985, transition: { duration: durations.fast, ease: easings.exit } },
};

export const page = {
  /* Без y-трансформа: сдвиг целой страницы под фиксированными барами
     с backdrop-filter заставляет их пересчитывать блюр каждый кадр —
     на мобиле это «виснет и дёргается». Fade дешёвый и плавный. */
  initial: { opacity: 0 },
  enter: { opacity: 1, transition: { duration: 0.22, ease: easings.enter } },
  exit: { opacity: 0, transition: { duration: 0.14, ease: easings.exit } },
};
