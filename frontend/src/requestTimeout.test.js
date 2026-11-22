import test from 'node:test';
import assert from 'node:assert/strict';
import { withRequestTimeout } from './requestTimeout.js';

test('returns a request result and aborts no completed request', async () => {
  const result = await withRequestTimeout(async signal => {
    assert.equal(signal.aborted, false);
    return 'ok';
  }, 50, 'timed out');
  assert.equal(result, 'ok');
});

test('rejects stalled requests with the configured timeout message', async () => {
  await assert.rejects(
    withRequestTimeout(signal => new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
    }), 10, 'timed out'),
    /timed out/,
  );
});

test('preserves errors that are unrelated to the timeout', async () => {
  await assert.rejects(withRequestTimeout(async () => { throw new Error('server error'); }, 50, 'timed out'), /server error/);
});
