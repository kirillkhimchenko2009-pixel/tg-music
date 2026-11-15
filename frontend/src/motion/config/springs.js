/* Физические пружины. Используются там, где движение должно ощущаться
   материальным: подложки навигации, шиты, обложка плеера, очереди.

   Плавность-пасс (15.11): жёсткость ниже, демпфирование выше —
   движение «скользит», а не отщёлкивает; массам добавили инерции. */
export const springs = {
  gentle: { type: 'spring', stiffness: 220, damping: 32, mass: 1 },
  snappy: { type: 'spring', stiffness: 300, damping: 36, mass: 1 },
  soft: { type: 'spring', stiffness: 150, damping: 30, mass: 1 },
};

/* Для layoutId-подложек: много демпфирования — подложка доехала и тишина,
   никакого «звона» на стыке экранов. */
export const layoutSpring = { type: 'spring', stiffness: 300, damping: 40, mass: 1 };

export default springs;
