/* Единый bottom-sheet: портал, затемнение, пружинный вход, закрытие
   свайпом только за грабер, Escape и клик вне. Контент внутри шита
   прокручивается свободно — вертикальный скролл не перехватывается. */
import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion, useDragControls } from 'motion/react';
import { overlayVariants, sheetVariants } from '../transitions/modalTransitions.js';
import { useReducedMotion } from '../hooks/useReducedMotion.js';

export default function Sheet({
  open, onClose, children, className = '', labelledBy, closeOnOverlay = true,
}) {
  const reduced = useReducedMotion();
  const controls = useDragControls();
  const panelRef = useRef(null);

  useEffect(() => {
    if (!open) return;

    const esc = e => { if (e.key === 'Escape') onClose?.(); };
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', esc);

    const t = setTimeout(() => {
      try { panelRef.current?.focus({ preventScroll: true }); } catch { /* noop */ }
    }, 40);

    return () => {
      clearTimeout(t);
      document.body.style.overflow = prevOverflow;
      window.removeEventListener('keydown', esc);
    };
  }, [open, onClose]);

  if (typeof document === 'undefined') return null;

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          className="shovl"
          variants={overlayVariants}
          initial="hidden"
          animate="visible"
          exit="exit"
          onClick={closeOnOverlay ? onClose : undefined}
        >
          <motion.div
            ref={panelRef}
            className={'sheet ' + className}
            variants={sheetVariants}
            initial="hidden"
            animate="visible"
            exit="exit"
            drag={reduced ? false : 'y'}
            dragControls={controls}
            dragListener={false}
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0, bottom: 0.55 }}
            onDragEnd={(_, info) => {
              if (info.offset.y > 96 || info.velocity.y > 720) onClose?.();
            }}
            onClick={e => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby={labelledBy}
            tabIndex={-1}
          >
            <span
              className="shgrab"
              aria-hidden="true"
              onPointerDown={e => { if (!reduced) controls.start(e); }}
            />
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
