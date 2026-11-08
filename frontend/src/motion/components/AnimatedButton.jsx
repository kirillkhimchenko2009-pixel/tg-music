/* Кнопка с физичным нажатием. hover/цвет остаются на CSS. */
import React from 'react';
import { motion } from 'motion/react';
import { springs } from '../config/springs.js';

export default function AnimatedButton({
  className = '', children, press = 0.94, type = 'button', ...rest
}) {
  return (
    <motion.button
      type={type}
      className={className}
      whileTap={{ scale: press }}
      transition={springs.snappy}
      {...rest}
    >
      {children}
    </motion.button>
  );
}
