export function filterPodcasts(shows, query = '', category = 'Все') {
  const needle = String(query).trim().toLocaleLowerCase('ru');
  return (shows || []).filter(show => {
    const matchesCategory = category === 'Все' || show.category === category;
    const searchable = `${show.title || show.t || ''} ${show.description || show.s || ''} ${show.category || ''}`
      .toLocaleLowerCase('ru');
    const episodeMatch = (show.episodes || []).some(episode =>
      `${episode.title || ''} ${episode.description || ''}`.toLocaleLowerCase('ru').includes(needle));
    return matchesCategory && (!needle || searchable.includes(needle) || episodeMatch);
  }).map(show => {
    if (!needle) return show;
    const showText = `${show.title || show.t || ''} ${show.description || show.s || ''} ${show.category || ''}`
      .toLocaleLowerCase('ru');
    return showText.includes(needle) ? show : {
      ...show,
      episodes: (show.episodes || []).filter(episode =>
        `${episode.title || ''} ${episode.description || ''}`.toLocaleLowerCase('ru').includes(needle)),
    };
  });
}

export function formatPodcastDuration(seconds = 0) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  return hours ? `${hours} ч ${String(minutes).padStart(2, '0')} мин` : `${minutes} мин`;
}

export function podcastTrackId(episodeId) {
  const prefix = String(episodeId || '').slice(0, 13);
  if (!/^[0-9a-f]{13}$/i.test(prefix)) throw new Error('Некорректный ID выпуска');
  return 4000000000000000 + parseInt(prefix, 16);
}

export function formatPodcastDate(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' })
    .format(date).replace(' г.', '');
}
