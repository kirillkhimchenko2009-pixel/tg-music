/* Карточки: hover/нажатие — Motion, масштаб изображения и кнопка Play — CSS.
   Так не конфликтуют два аниматора на одном transform. */
import { durations } from '../config/durations.js';
import { easings } from '../config/easings.js';
import { springs } from '../config/springs.js';

export const cardHover = {
  y: -3,
  transition: { duration: durations.normal, ease: easings.standard },
};

export const cardTap = {
  scale: 0.985,
  transition: { duration: durations.fast, ease: easings.standard },
};

/* Для элементов, у которых нет CSS-анимации на том же transform. */
export const cardHoverSoft = {
  y: -2,
  transition: springs.snappy,
};

export const tileHover = {
  y: -2,
  transition: { duration: durations.normal, ease: easings.standard },
};
