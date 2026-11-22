export function audiobookTrackId(bookId, chapterId) {
  const book = Number(bookId);
  const chapter = Number(chapterId);
  if (!Number.isSafeInteger(book) || book < 1 || book > 200000 ||
      !Number.isSafeInteger(chapter) || chapter < 1 || chapter > 999999) {
    throw new Error('Некорректный ID главы аудиокниги');
  }
  return 6000000000000000 + book * 1000000 + chapter;
}

export function formatBookDuration(seconds = 0) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  return hours ? `${hours} ч ${String(minutes).padStart(2, '0')} мин` : `${minutes} мин`;
}

export function filterBooks(books, language = 'Все') {
  if (language === 'Все') return books || [];
  const needle = language.toLocaleLowerCase('ru');
  return (books || []).filter(book => String(book.language || '').toLocaleLowerCase('ru') === needle);
}
