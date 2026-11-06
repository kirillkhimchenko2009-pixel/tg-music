/* Прикладная навигация Vibe: хелперы над стек-роутером nav.js. */
import { navPush } from './nav.js';

export function goTab(tab, sub = null) {
  navPush({ tab, sub });
}

export function goWave() {
  navPush({ tab: 'wave' });
}

/* pl: {id, title, cover_url?, count?} */
export function goPlaylist(pl) {
  navPush({ tab: 'playlist', id: pl.id, title: pl.title, cover_url: pl.cover_url || null, count: pl.count ?? null });
}

export function goArtist(name, cover = null) {
  navPush({ tab: 'artist', name, cover });
}

/* album: { key, title, artist, year, cover, tracks } */
export function goAlbum(album) {
  navPush({ tab: 'album', album });
}

export function goSearch(q) {
  navPush({ tab: 'search', q: q || '', sub: null });
}
