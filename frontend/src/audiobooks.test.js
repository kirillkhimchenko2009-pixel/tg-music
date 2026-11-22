import test from 'node:test';
import assert from 'node:assert/strict';
import { audiobookTrackId, filterBooks, formatBookDuration } from './audiobooks.js';

test('creates stable safe local IDs for audiobook chapter tracks', () => {
  assert.equal(Number.isSafeInteger(audiobookTrackId('253', '124135')), true);
  assert.equal(audiobookTrackId('253', '124135'), audiobookTrackId(253, 124135));
  assert.throws(() => audiobookTrackId('x', '1'), /ID главы/);
  assert.equal(Number.isSafeInteger(audiobookTrackId('200000', '999999')), true);
  assert.throws(() => audiobookTrackId('200001', '1'), /ID главы/);
});

test('filters books by language and formats long durations', () => {
  const books = [{ language: 'English' }, { language: 'Russian' }, { language: 'French' }];
  assert.equal(filterBooks(books, 'Все').length, 3);
  assert.deepEqual(filterBooks(books, 'russian'), [books[1]]);
  assert.equal(formatBookDuration(47204), '13 ч 06 мин');
  assert.equal(formatBookDuration(75), '1 мин');
});
