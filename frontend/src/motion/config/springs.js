/* Физические пружины. Используются там, где движение должно ощущаться
   материальным: подложки навигации, шиты, обложка плеера, очереди.

   Soft-пасс: ниже жёсткость, выше демпфирование — движение «плывёт»
   и бесшумно доезжает, никакого подскока на финише. */
export const springs = {
  gentle: { type: 'spring', stiffness: 190, damping: 34, mass: 1 },
  snappy: { type: 'spring', stiffness: 260, damping: 38, mass: 1 },
  soft: { type: 'spring', stiffness: 140, damping: 34, mass: 1 },
};

/* Для layoutId-подложек: много демпфирования — подложка доехала и тишина,
   никакого «звона» на стыке экранов. */
export const layoutSpring = { type: 'spring', stiffness: 260, damping: 42, mass: 1 };

export default springs;
