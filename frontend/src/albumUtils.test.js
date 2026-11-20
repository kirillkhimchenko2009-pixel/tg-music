import assert from 'node:assert/strict';
import test from 'node:test';
import { dedupeAlbumTracks } from './albumUtils.js';

test('dedupes identical track IDs but preserves separately catalogued versions', () => {
  const tracks = [
    { id: 5, source: 'yandex', title: 'Что такое осень', duration: 298 },
    { id: 5, source: 'yandex', title: 'Что такое — осень', duration: 298 },
    { id: 8, source: 'yandex', title: 'Что такое осень', duration: 298 },
    { id: 5, source: 'local', title: 'Что такое осень', duration: 298 },
  ];
  assert.deepEqual(dedupeAlbumTracks(tracks), [tracks[0], tracks[2], tracks[3]]);
});

test('keeps tracks lacking stable provider identifiers', () => {
  const tracks = [{ id: 1, title: 'Song' }, { id: 1, title: 'Song' }];
  assert.deepEqual(dedupeAlbumTracks(tracks), tracks);
});
