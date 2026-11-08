/* Базовая модалка (центр/низ) с затемнением и мягким scale.
   Для шитов используем Sheet — он добавляет свайп и нижнюю геометрию. */
import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion } from 'motion/react';
import { modalVariants, overlayVariants } from '../transitions/modalTransitions.js';

export default function AnimatedModal({
  open, onClose, children, className = 'modal-card', overlayClassName = 'shovl',
}) {
  useEffect(() => {
    if (!open) return;
    const esc = e => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [open, onClose]);

  if (typeof document === 'undefined') return null;

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          className={overlayClassName}
          variants={overlayVariants}
          initial="hidden"
          animate="visible"
          exit="exit"
          onClick={onClose}
        >
          <motion.div
            className={className}
            variants={modalVariants}
            initial="hidden"
            animate="visible"
            exit="exit"
            onClick={e => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
