/* Каталог аудиокниг живёт в двух источниках: числовые ID LibriVox API и
   строковые идентификаторы Internet Archive (напр. «ushkuiniks_1903_librivox»).
   Плеер/офлайн-хранилище ждут безопасное целое → строковые книги хешируем
   (FNV-1a) в ключ 1..200000, а главу оставляем как есть (1..999999). */
const BOOK_KEY_MAX = 200000;

function bookKeyFrom(bookId) {
  const numeric = Number(bookId);
  if (Number.isFinite(numeric)) {
    // числовой ID LibriVox: строгая валидация диапазона
    return Number.isSafeInteger(numeric) && numeric >= 1 && numeric <= BOOK_KEY_MAX
      ? numeric : null;
  }
  if (typeof bookId === 'string' && bookId && bookId.length <= 244) {
    let hash = 0x811c9dc5;
    for (let i = 0; i < bookId.length; i += 1) {
      hash ^= bookId.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return (hash % BOOK_KEY_MAX) + 1;
  }
  return null;
}

export function audiobookTrackId(bookId, chapterId) {
  const bookKey = bookKeyFrom(bookId);
  const chapter = Number(chapterId);
  if (!Number.isSafeInteger(bookKey) ||
      !Number.isSafeInteger(chapter) || chapter < 1 || chapter > 999999) {
    throw new Error('Некорректный ID главы аудиокниги');
  }
  return 6000000000000000 + bookKey * 1000000 + chapter;
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
