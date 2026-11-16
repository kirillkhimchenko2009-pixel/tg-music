/* Кривые ускорения Motion. Массивы — для Motion, строки — для CSS.
   Soft-пасс: выходы заканчиваются с нулевой скоростью (без «обрыва»),
   emphasized стартует тише — переходы без острых краёв. */
export const easings = {
  standard: [0.2, 0, 0, 1],
  enter: [0.16, 1, 0.3, 1],
  exit: [0.45, 0, 0.3, 1],
  emphasized: [0.25, 0.65, 0.25, 1],
  linear: 'linear',
};

export const cssEasings = {
  standard: 'cubic-bezier(.2, 0, 0, 1)',
  enter: 'cubic-bezier(.16, 1, .3, 1)',
  exit: 'cubic-bezier(.45, 0, .3, 1)',
  emphasized: 'cubic-bezier(.25, .65, .25, 1)',
  linear: 'linear',
};

export default easings;
