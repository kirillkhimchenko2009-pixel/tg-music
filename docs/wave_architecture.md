# Архитектура интеллектуальной «Волны» (этап 2)

Документ проектирования по мастер-плану (этап 2 из 13). Этап 1 (аудит) завершён,
карта компонентов ниже — его результат. Собственный алгоритм; rotor ЯМ (движок A)
не переписывается и не клонируется.

---

## 1. Карта существующих компонентов (аудит)

| Зона | Файлы | Состояние |
|---|---|---|
| БД | `core/models.py` (User, Track, Playlist/PlaylistTrack, WaveSettings, WaveSession, PlayEvent, TrackFeedback), `music/models.py` (отдельная музыкальная подсистема) | SQLAlchemy async, SQLite/Postgres, `init_db()` c create_all + мини-миграции |
| Волна | `services/wave/service.py` (start/next/feedback/settings), `own_engine.py` (движок B: taste/score/next_batch), `yandex_rotor.py` (движок A), `prefetch.py` (прогрев стримов + партия B) | Работает; BATCH=3, скипы <25с = жёсткий сигнал, дисклайк = мораторий |
| API | `server/routes/wave.py` (`/api/wave/start|next|feedback|settings`), auth через initData/Bearer | Pydantic-контракты |
| Каталог | `services/catalog.py` (upsert/search/get/meta), провайдеры `services/providers/*` | Лайки = плейлист «❤%» |
| Плеер | `frontend/src/player.js` (singleton Audio, wave-source, beacon /api/history) | Единый плеер приложения |
| UI | `frontend/src/screens/Wave.jsx` (FormulaSheet, контролы, история), `motion/*` (Motion Design System) | React 18 + Vite, JS |

Стек фактический: Python/FastAPI + SQLite(aiosqlite)/Postgres + aiogram 3.31 +
React 18/Vite (JS) + motion/react. PostgreSQL/Redis не внедряем — данных мало,
архитектура остаётся переносимой (JSON-поля вместо JSONB, naive-UTC datetime).

## 2. Восемь подсистем → файлы

Новый пакет `services/wave/intel/` (разделение ответственности, без одного файла-бога):

| Модуль промпта | Файл | Ответственность |
|---|---|---|
| M1 User Taste Engine | `intel/taste.py` | Долгосрочный профиль с затуханием, уровни A/B/C, разделение long/session/context |
| M2 Context Intelligence | `intel/context.py` | Сигналы (время, день, сессия, занятие, настроение), приоритеты, сценарии с честными названиями |
| M3 Recommendation Engine | `intel/sources.py`, `intel/ranker.py` | 8 источников кандидатов в едином интерфейсе; нормализованный скоринг с конфигурируемыми весами |
| M4 Endless Wave Engine | `intel/endless.py`, `intel/sequence.py` | Бесконечная очередь, sequence optimizer, защита от повторов (поверх существующего prefetch) |
| M5 User Feedback Engine | `intel/feedback.py`, `intel/events.py` | Гранулярные события §7.1, классификация скипов, дизлайк с причинами, обновление профиля |
| M6 Artist Discovery | `intel/discovery.py` | Новые артисты, профили артистов, реплики только из проверяемых метаданных каталога |
| M7 Wave Visual | `frontend/src/screens/Wave.jsx`, `intel/context.py` (палитры) | Сценарные палитры, слоистая анимация, shared transitions (motion/react) |
| M8 Optimization Engine | `intel/metrics.py` | Метрики §17 (completion/skip/diversity/context match...), конфиг весов отдельно от кода |
| Shake | `intel/shake.py` | Альтернативный сценарий без обнуления вкуса; frontend `frontend/src/shake.js` (DeviceMotion) |

Не трогаем: `yandex_rotor.py` (движок A), legacy-функции, pipeline загрузок.

## 3. Схемы данных (новые таблицы/колонки)

```python
class WaveEvent:        # события §7.1
    id, user_id, track_id?, artist_key?, session_id?,
    kind            # impression|track_started|played_10s|played_30s|played_50p|
                    # played_90p|completed|skipped|replayed|seeked|liked|unliked|
                    # disliked(+reason)|added_to_library|removed_from_library|
                    # artist_opened|search_selected|context_selected|
                    # wave_refreshed|wave_shaken
    reason?         # причина дизлайка/скипа (composition|artist|genre|repeat|mood|too_energy|too_calm)
    context: JSON   # {time_of_day, weekday, device, activity?, mood?}
    scenario?       # id активного сценария
    played_seconds, track_duration, created_at

class TasteProfile:     # LONG-TERM (не смешивается с сессией)
    user_id PK, artist_affinity JSON, genre_affinity JSON, mood_affinity JSON,
    language_affinity JSON, context_affinity JSON,   # устойчивые контекстные привычки
    explore_share Float, excluded_artists JSON, excluded_genres JSON,
    refreshed_at

class ArtistStats:      # интерес к артисту + discovery
    id, user_id, artist_key, display_name, plays, likes, dislikes, skips,
    completions, first_seen_at, last_played_at, discovered_via, is_excluded

class WaveKV:           # конфигурация весов отдельно от кода + editorial-пул
    key PK, value JSON, updated_at
```

Мини-миграции в `core/db.init_db()`: `wave_sessions.scenario JSON`, `wave_sessions.context JSON`.

SESSION TASTE — не хранится в профиле: считается из активной сессии
(played_track_ids + события последних 30 мин). CURRENT CONTEXT — из сигналов
сцены + context_affinity.

## 4. Скоринг (M3)

```
Score = 0.30·TasteFit + 0.25·ContextFit + 0.15·SessionFit + 0.12·Discovery
      + 0.08·AudioQuality + 0.10·Freshness
      − RepeatPenalty − DislikePenalty − ArtistFatiguePenalty
```

- Все факторы нормализованы в 0..1 (кроме штрафов, 0..1.5).
- Веса в `WaveKV["weights"]`, код читает из конфиг-слоя (`intel/config.py`),
  дефолты — константы модуля. Жёсткие лимиты (моратории, cooldown) — тоже там.
- Аудио-параметры (BPM/Energy) НЕ выдумываются: AudioQuality — из реальных
  метаданных (quality/size/duration), similarity — по жанрово-настроенческим
  словам каталога + артистам, т.к. тегов жанров в БД нет.
- Доля открытий 75–85% знакомого / 15–25% нового, адаптивная
  (`profile.explore_share` реагирует на реакции на новые треки).

Источники кандидатов (единый `Candidate`): content / artist / cf / session /
context / discovery / editorial (из WaveKV, если заведён; иначе источник
молчит) / library. Каждый источник изолирован try/except — отказ одного не
валит выдачу (§19).

## 5. Контекст и сценарии (M2)

Приоритет сигналов: явный выбор → сессия → устойчивые контекстные привычки →
долгосрочный профиль → время суток/день → устройство. Геолокации нет. Контекст
отключаем (WaveSettings.context_enabled + правлено в UI), событие
`context_selected` пишет явный выбор.

Сценарий = {id, title, description, palette, explore_share, filters}.
Название честное: заголовок собирается из фактических фильтров, а авто-сценарий
получает имя «Для тебя», если словесных подтверждений в пуле мало (нельзя
назвать «спокойной» волну с агрессивными треками — проверка по пулу до отдачи).

## 6. API-контракты (новые ручки)

```
GET  /api/wave/scenarios        → {scenarios: [ScenarioDTO]} (карточки волны)
POST /api/wave/start            → + scenario в DTO
POST /api/wave/next             → + scenario в DTO
POST /api/wave/event            {session_id, kind, track_id?, played_seconds?,
                                 reason?, device?} → {ok}
POST /api/wave/shake            {session_id} → {scenario, tracks}
GET  /api/wave/taste            → профиль вкуса (уровни A/B/C, исключения)
POST /api/wave/taste/exclude    {artist_key} / {genre} → {ok}
POST /api/wave/taste/include    {artist_key} / {genre} → {ok}
POST /api/wave/clear            {scope: history|taste|all} → {ok}  (приватность)
GET  /api/wave/metrics          → метрики юзера (§17)
```

Обратная совместимость: существующие поля `/start` и `/next` не меняются, новые
добавляются. `/feedback` остаётся (ротар-путь движка A), для движка B события
идут через `/event`.

## 7. Event Model (§7.1)

Плеер (`player.js`) в волне отправляет: impression (показ трека в очереди),
started, played_10s/30s/50p/90p, completed, skipped (+ позиция: first_seconds /
few_seconds / mid / near_end / after_similar), replayed, seeked, liked/disliked
(+reason), added/removed. Каждый шаг волнового цикла уже пишет PlayEvent — он
остаётся ядром вкуса; WaveEvent — детальная телеметрия для метрик и обучения.

Один скип ≠ дизлайк: лёгкие скипы уменьшают session_fit трека/артиста в сессии
и слабо давят на freshness; жёсткий (<10с) повторный скип — заморозка трека
(конфиг `skip_freeze_days`), но не мораторий артиста.

## 8. Этапы реализации (3→13)

1. Этап 3 (Visual) — после бэкенд-ядра: палитры сценариев приходят из API.
2. Этапы 4–6 (профиль/контекст/рекомендации) — пакет `intel/`, интеграция в
   `service.py` (движок B → новый pipeline, старый `own_engine` — фолбэк).
3. Этап 7 (endless) — `endless.py` + prefetch: держим ≥2 партии впереди.
4. Этап 8 (feedback) — `/api/wave/event` + обновление профиля и ArtistStats.
5. Этап 9 (discovery) — discovery-слоты, профили артистов, реплики.
6. Этап 10 (shake) — `/shake` + DeviceMotion-хук.
7. Этапы 11–12 (ML/оптимизация) — только метрики сейчас; ML при накоплении
   событий (порог в конфиге).
8. Этап 13 (интеграция) — e2e через мок Bot API, полный pytest, пересборка
   `frontend/dist`, коммиты по этапам.

Приватность: профили изолированы по user_id, `context_enabled=false` убирает
контекстную персонализацию, `/clear` чистит историю/вкус, лишние персональные
данные не пишутся (только user_id/track/artist/события).
