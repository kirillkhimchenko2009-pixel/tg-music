/* Уведомления: единая точка выхода, layout-перестановка без скачков. */
import React, { useSyncExternalStore } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { dismissToast, getToasts, subscribeToasts } from '../utils/toast.js';
import { springs } from '../config/springs.js';

export default function ToastHost() {
  const items = useSyncExternalStore(subscribeToasts, getToasts, getToasts);

  return (
    <div className="toast-host" aria-live="polite" aria-atomic="false">
      <AnimatePresence initial={false}>
        {items.map(t => (
          <motion.div
            key={t.id}
            layout
            className={'toast toast-' + t.kind}
            initial={{ opacity: 0, y: 14, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.97 }}
            transition={springs.gentle}
            role="status"
          >
            <span className="toast-ic" aria-hidden="true">
              {t.icon || (t.kind === 'success' ? '✓' : t.kind === 'error' ? '!' : '•')}
            </span>
            <span className="toast-tx">{t.message}</span>
            <button className="toast-x" onClick={() => dismissToast(t.id)} aria-label="Закрыть уведомление">×</button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
