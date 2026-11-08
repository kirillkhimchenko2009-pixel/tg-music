/* Выпадающее меню/поповер с единым входом и выходом. */
import React from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { popVariants } from '../transitions/modalTransitions.js';

export default function AnimatedDropdown({ show, className = 'pop', children, ...rest }) {
  return (
    <AnimatePresence initial={false}>
      {show && (
        <motion.div
          className={className}
          variants={popVariants}
          initial="hidden"
          animate="visible"
          exit="exit"
          {...rest}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
