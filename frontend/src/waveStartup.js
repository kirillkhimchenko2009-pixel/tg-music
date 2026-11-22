/* Decide whether Wave should restore an already-active station on screen entry. */
export function initialWavePhase({ inWave, queueLength }) {
  return inWave && queueLength > 0 ? 'ready' : 'idle';
}
