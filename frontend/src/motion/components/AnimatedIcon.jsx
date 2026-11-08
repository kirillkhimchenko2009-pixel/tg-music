/* Плавная замена иконки по назначению (скачать → спиннер → галочка). */
import React from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { durations } from '../config/durations.js';
import { easings } from '../config/easings.js';

export default function AnimatedIcon({ name, className = '', children, ...rest }) {
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.span
        key={name}
        className={className}
        initial={{ opacity: 0, scale: 0.74, rotate: -7 }}
        animate={{ opacity: 1, scale: 1, rotate: 0 }}
        exit={{ opacity: 0, scale: 0.74, rotate: 7 }}
        transition={{ duration: durations.fast, ease: easings.enter }}
        style={{ display: 'inline-grid', placeItems: 'center' }}
        {...rest}
      >
        {children}
      </motion.span>
    </AnimatePresence>
  );
}
