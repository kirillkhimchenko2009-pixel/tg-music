/* Удобный ре-экспорт токенов для экранов и компонентов. */
export { durations, durationMs } from '../config/durations.js';
export { easings, cssEasings } from '../config/easings.js';
export { springs, layoutSpring } from '../config/springs.js';
export * from '../config/variants.js';

export const staggerDelay = (index, step = 0.04, max = 6) => Math.min(index, max) * step;
