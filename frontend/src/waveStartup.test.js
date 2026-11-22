import test from 'node:test';
import assert from 'node:assert/strict';
import { initialWavePhase } from './waveStartup.js';

test('restores the Wave screen when an active Wave queue exists', () => {
  assert.equal(initialWavePhase({ inWave: true, queueLength: 1 }), 'ready');
});

test('shows the explicit start state for empty Wave queues or ordinary queues', () => {
  assert.equal(initialWavePhase({ inWave: true, queueLength: 0 }), 'idle');
  assert.equal(initialWavePhase({ inWave: false, queueLength: 4 }), 'idle');
});
