/* Публичный вход Motion Design System Vibe. */
export { durations, durationMs } from './config/durations.js';
export { easings, cssEasings } from './config/easings.js';
export { springs, layoutSpring } from './config/springs.js';
export * from './config/variants.js';
export { motionConfig } from './config/motionConfig.js';

export * from './transitions/pageTransitions.js';
export * from './transitions/modalTransitions.js';
export * from './transitions/playerTransitions.js';
export * from './transitions/cardTransitions.js';
export * from './transitions/navigationTransitions.js';

export { useReducedMotion } from './hooks/useReducedMotion.js';
export { useEnterOnce, markVisited, resetVisited } from './hooks/useEnterOnce.js';
export { useSectionReveal } from './hooks/useSectionReveal.js';
export { usePageTransition } from './hooks/usePageTransition.js';
export { usePlayerMotion } from './hooks/usePlayerMotion.js';
export { useSharedTransition } from './hooks/useSharedTransition.js';

export { default as AnimatedCard } from './components/AnimatedCard.jsx';
export { default as AnimatedButton } from './components/AnimatedButton.jsx';
export { default as AnimatedModal } from './components/AnimatedModal.jsx';
export { default as AnimatedDropdown } from './components/AnimatedDropdown.jsx';
export { default as AnimatedList, AnimatedItem } from './components/AnimatedList.jsx';
export { default as AnimatedIcon } from './components/AnimatedIcon.jsx';
export { default as Sheet } from './components/Sheet.jsx';
export { default as Seek } from './components/Seek.jsx';
export { default as ToastHost } from './components/ToastHost.jsx';
export { default as Reveal } from './components/Reveal.jsx';

export { toast, dismissToast } from './utils/toast.js';
export { staggerDelay } from './utils/motionPresets.js';
export { stopAnimations, cleanupMotion } from './utils/animationCleanup.js';
