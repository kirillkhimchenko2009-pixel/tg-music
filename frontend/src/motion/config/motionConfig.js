/* Глобальная конфигурация Motion: единая длительность и кривая по умолчанию,
   уважение к prefers-reduced-motion (Motion сам убирает transform/layout). */
import { durations } from './durations.js';
import { easings } from './easings.js';

export const motionConfig = {
  transition: { duration: durations.normal, ease: easings.standard },
  reducedMotion: 'user',
};

export default motionConfig;
