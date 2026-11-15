/* Единая шкала времени Vibe Motion.
   В Motion for React длительности задаются в секундах, поэтому базовые
   значения хранятся здесь, а CSS-токены (--dur-*) повторяют их в ms. */
export const durations = {
  instant: 0.10,     // hover, цвет, прозрачность
  fast: 0.18,        // нажатия, переключатели, мелкие иконки
  normal: 0.26,      // карточки, меню, небольшие панели
  medium: 0.38,      // боковые панели, списки, смена состояния
  slow: 0.54,        // полноэкранный плеер, большие модалки
  cinematic: 0.72,   // особые переходы и крупные трансформации
};

export const durationMs = {
  instant: 100,
  fast: 180,
  normal: 260,
  medium: 380,
  slow: 540,
  cinematic: 720,
};

export default durations;
