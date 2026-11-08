/* Карточка с единым входом/нажатием. CSS отвечает за масштаб обложки
   и появление Play, Motion — за подъём, тап и stagger. */
import React from 'react';
import { motion } from 'motion/react';
import { cardRise } from '../config/variants.js';
import { cardTap } from '../transitions/cardTransitions.js';
import { easings } from '../config/easings.js';
import { durations } from '../config/durations.js';

export default function AnimatedCard({
  as = 'div', delay = 0, instant = false, hover = true,
  className = '', children, ...rest
}) {
  const C = motion[as] || motion.div;
  return (
    <C
      className={className}
      initial={instant ? false : 'hidden'}
      animate="visible"
      variants={cardRise}
      custom={Math.min(Math.max(delay, 0), 6) * 0.04}
      whileHover={hover ? { y: -3, transition: { duration: durations.normal, ease: easings.standard } } : undefined}
      whileTap={cardTap}
      {...rest}
    >
      {children}
    </C>
  );
}
