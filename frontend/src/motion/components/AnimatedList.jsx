/* Списки: layout-перестановки и мягкое появление строк.
   AnimatePresence остаётся у владельца списка — так проще управлять exit. */
import React from 'react';
import { motion } from 'motion/react';
import { listItem } from '../config/variants.js';
import { springs } from '../config/springs.js';

export function AnimatedItem({ as = 'div', i = 0, className = '', children, ...rest }) {
  const C = motion[as] || motion.div;
  return (
    <C
      layout
      className={className}
      variants={listItem}
      initial="hidden"
      animate="visible"
      exit="exit"
      transition={{ ...springs.gentle, delay: Math.min(Math.max(i, 0), 12) * 0.026 }}
      {...rest}
    >
      {children}
    </C>
  );
}

export default function AnimatedList({ className = '', children }) {
  return <div className={className}>{children}</div>;
}
