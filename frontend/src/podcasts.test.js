import test from 'node:test';
import assert from 'node:assert/strict';
import { filterPodcasts, formatPodcastDate, formatPodcastDuration, podcastTrackId } from './podcasts.js';

const shows = [
  { t: 'Дизайн слышимого', s: 'Интерфейсы', category: 'Дизайн' },
  { t: 'Ночные эфиры', s: 'Лоу-фай и музыка', category: 'Музыка' },
  { t: 'Первый дубль', s: 'Кино и культура', category: 'Культура' },
];

test('filters podcast previews by category', () => {
  assert.deepEqual(filterPodcasts(shows, '', 'Музыка'), [shows[1]]);
});

test('searches names, descriptions, and categories case-insensitively', () => {
  assert.deepEqual(filterPodcasts(shows, 'ЛОУ-ФАЙ'), [shows[1]]);
  assert.deepEqual(filterPodcasts(shows, 'культура'), [shows[2]]);
});

test('combines search and category and returns no false matches', () => {
  assert.deepEqual(filterPodcasts(shows, 'дизайн', 'Музыка'), []);
  assert.deepEqual(filterPodcasts(shows, 'нет такого'), []);
  assert.deepEqual(filterPodcasts(shows), shows);
});

test('searches episode titles and narrows the result to matching episodes', () => {
  const liveShows = [{ title: 'Студия', description: 'Разговоры', category: 'Музыка', episodes: [
    { title: 'История синтезатора', description: 'От первых волн до сегодня' },
    { title: 'Городские легенды', description: 'Истории ночного города' },
  ] }];
  const result = filterPodcasts(liveShows, 'синтезатора');
  assert.equal(result.length, 1);
  assert.deepEqual(result[0].episodes.map(episode => episode.title), ['История синтезатора']);
});

test('formats podcast durations and dates safely', () => {
  assert.equal(formatPodcastDuration(3723), '1 ч 02 мин');
  assert.equal(formatPodcastDuration(125), '2 мин');
  assert.equal(formatPodcastDate('not a date'), '');
  assert.equal(Number.isSafeInteger(podcastTrackId('deadbeef000000000000')), true);
  assert.throws(() => podcastTrackId('invalid'), /ID выпуска/);
});
