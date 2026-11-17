/* LOCAL-FIRST ЯДРО VIBE v3 (P0): локальная база метаданных/состояния.
   Слои (мастер-промпт §2): A remote library — сервер; B local storage —
   OPFS/IndexedDB (storage.js); C local database — здесь (Dexie); D
   playback engine — player.js через репозиторий (repo.js).
   Схема версионированная: новые таблицы/индексы — новая db.version(N).
   Тяжёлых операций в транзакциях нет: только put/get/delete. */
import Dexie from 'dexie';

export const db = new Dexie('vibe-local');

db.version(1).stores({
  // кеш метаданных серверной библиотеки (A→C снапшот; файлы — в storage.js)
  tracks: 'id, artist_id, album_id, date_added, last_played, dl_state, hash',
  artists: 'id',
  albums: 'id, artist_id, date_added',
  playlists: 'id, date_added',
  playlist_tracks: '++row, playlist_id, track_id, [playlist_id+position]',
  favorites: 'track_id, date_added',

  // история/очередь/состояние плеера
  playback_history: '++id, track_id, at',
  playback_queue: '++pos',

  // менеджер загрузок (resumable): очередь воркер-независимая, персистентная
  download_jobs: 'track_id, state, created',

  // физические файлы: манифест по hash (дедуп), тексты, пики волны
  storage_manifest: 'hash, track_id',
  track_lyrics: 'track_id',
  waveform_peaks: 'track_id',

  // настройки + офлайн-события (идемпотентная синхронизация по event_id)
  user_settings: 'key',
  offline_events: 'event_id, state, at',

  // фолбэк-хранилище аудио/недокачанных частей, если OPFS недоступен
  audio_files: 'key',
  audio_tmp: 'key',
});
