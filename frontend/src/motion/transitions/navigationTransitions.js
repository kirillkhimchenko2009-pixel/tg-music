/* Навигация: подложка активного пункта переезжает между элементами. */
import { layoutSpring, springs } from '../config/springs.js';
import { durations } from '../config/durations.js';
import { easings } from '../config/easings.js';

export const navIndicator = { layout: layoutSpring };
export const tabIndicator = { layout: layoutSpring };
export const segmentIndicator = { layout: layoutSpring };

export const sidebarIndicatorTransition = layoutSpring;
export const tabbarIndicatorTransition = layoutSpring;

export const navLabelTransition = { duration: durations.fast, ease: easings.standard };
export const navPress = { scale: 0.97, transition: springs.snappy };
