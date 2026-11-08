/* Единая шкала времени Vibe Motion.
   В Motion for React длительности задаются в секундах, поэтому базовые
   значения хранятся здесь, а CSS-токены (--dur-*) повторяют их в ms. */
export const durations = {
  instant: 0.10,     // hover, цвет, прозрачность
  fast: 0.16,        // нажатия, переключатели, мелкие иконки
  normal: 0.22,      // карточки, меню, небольшие панели
  medium: 0.32,      // боковые панели, списки, смена состояния
  slow: 0.48,        // полноэкранный плеер, большие модалки
  cinematic: 0.64,   // особые переходы и крупные трансформации
};

export const durationMs = {
  instant: 100,
  fast: 160,
  normal: 220,
  medium: 320,
  slow: 480,
  cinematic: 640,
};

export default durations;
