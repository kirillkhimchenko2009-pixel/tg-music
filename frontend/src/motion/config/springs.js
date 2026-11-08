/* Физические пружины. Используются там, где движение должно ощущаться
   материальным: подложки навигации, шиты, обложка плеера, очереди. */
export const springs = {
  gentle: { type: 'spring', stiffness: 260, damping: 30, mass: 1 },
  snappy: { type: 'spring', stiffness: 420, damping: 34, mass: 0.8 },
  soft: { type: 'spring', stiffness: 180, damping: 26, mass: 1 },
};

/* Для layoutId-подложек: чуть больше демпфирования, чтобы не «звенело». */
export const layoutSpring = { type: 'spring', stiffness: 380, damping: 38, mass: 0.9 };

export default springs;
