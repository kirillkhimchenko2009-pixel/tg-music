/* Кривые ускорения Motion. Массивы — для Motion, строки — для CSS. */
export const easings = {
  standard: [0.2, 0, 0, 1],
  enter: [0.16, 1, 0.3, 1],
  exit: [0.4, 0, 1, 1],
  emphasized: [0.2, 0.8, 0.2, 1],
  linear: 'linear',
};

export const cssEasings = {
  standard: 'cubic-bezier(.2, 0, 0, 1)',
  enter: 'cubic-bezier(.16, 1, .3, 1)',
  exit: 'cubic-bezier(.4, 0, 1, 1)',
  emphasized: 'cubic-bezier(.2, .8, .2, 1)',
  linear: 'linear',
};

export default easings;
