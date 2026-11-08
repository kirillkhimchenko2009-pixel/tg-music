"""Download Manager: разрешение → файл → качество → обложка → метаданные → кэш."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..cache.music_cache import store_cached
from ..config import MusicSettings, music_settings
from ..domain import (AudioAnalysis, AudioProbe, DownloadOutcome, MediaKind,
                      PermissionInfo, PreparedTrack, TrackCandidate)
from ..enums import JobStatus, PermissionStatus, QualityCategory
from ..errors import (CancelledError as JobCancelled, DownloadError,
                      FileTooLarge, InvalidContent, MusicError,
                      PermissionDenied, ProcessingError)
from ..net import SafeHttpClient
from ..processing.converter import audio_format, media_kind_for, mime_for, telegram_filename
from ..processing.cover_processor import (prepare_cover, store_cover_copy)
from ..processing.metadata_editor import MetadataFields, write_metadata
from ..quality.analyzer import analyze_audio
from ..quality.cover_checker import analyze_image
from ..storage import (check_disk_space, cleanup_job_dir, file_size,
                       guess_extension, quarantine, sha256_file, temp_file,
                       workspace_for)
from .file_handler import copy_local, finalize
from .permission import PermissionChecker, permission_checker

log = logging.getLogger("music.downloader")

ProgressFn = Callable[[str, int, str], Any]


async def emit(progress: ProgressFn | None, step: str, percent: int, detail: str = "") -> None:
    if progress is None:
        return
    try:
        result = progress(step, max(0, min(100, int(percent))), detail)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # noqa: BLE001
        log.debug("progress callback: %s", exc)


class DownloadManager:
    def __init__(self, settings: MusicSettings | None = None,
                 checker: PermissionChecker | None = None):
        self.settings = settings or music_settings
        self.checker = checker or permission_checker

    async def prepare(self, job_id: str, user_id: int, candidate: TrackCandidate, *,
                      provider=None, session=None, progress: ProgressFn | None = None,
                      cancel: Any | None = None) -> DownloadOutcome:
        if provider is None:
            from ..providers import get_provider
            provider = get_provider(candidate.provider)
        if provider is None:
            return DownloadOutcome(ok=False, job_id=job_id, error_code="PROVIDER_UNKNOWN",
                                   error="Источник не зарегистрирован",
                                   official_url=candidate.source_url)
        workspace = workspace_for(job_id)
        moved_to_cache = False
        keep_workspace = False
        keep_cover = False
        official_url = candidate.source_url or ""
        permission: PermissionInfo | None = None
        cover_path: str | None = None
        try:
            self._check_cancel(cancel)
            check_disk_space()
            await emit(progress, "permission", 5, "Проверка разрешения…")
            permission = await self.checker.check(provider, candidate)
            official_url = permission.source_url or official_url
            if not permission.can_download:
                return DownloadOutcome(
                    ok=False, job_id=job_id, permission=permission,
                    official_url=official_url, error_code=permission.status.value,
                    error=permission.reason or permission.label)
            await emit(progress, "permission", 10, "✅ Скачивание разрешено")

            source_key = hashlib.sha256(
                f"{candidate.provider}:{candidate.external_id}".encode()).hexdigest()[:32]
            if session is not None:
                from .. import repository
                cached = await repository.get_cached(session, source_key)
                cached_prepared = await self._from_cache(cached, candidate, source_key)
                if cached_prepared is not None:
                    await emit(progress, "cache", 100, "✅ Файл уже в кэше")
                    return DownloadOutcome(ok=True, job_id=job_id,
                                           prepared=cached_prepared, permission=permission)

            self._check_cancel(cancel)
            await emit(progress, "download", 12, "📥 Загрузка аудиофайла…")
            audio_source = await provider.get_audio_stream(candidate)
            local_path = str((audio_source.extra or {}).get("local_path") or "")
            content_type = audio_source.mime or ""
            if local_path:
                path = copy_local(local_path, workspace, job_id=job_id)
            else:
                if not audio_source.url:
                    raise DownloadError("Источник не выдал ссылку на файл",
                                        code="NO_DOWNLOAD_URL")
                suffix = guess_extension(content_type, audio_source.url,
                                         "." + (audio_source.expected_format or "bin"))
                if audio_source.filename:
                    suffix = Path(audio_source.filename).suffix or suffix
                dest = temp_file(job_id, suffix or ".bin", candidate.title or "audio")

                async def on_bytes(done: int, total: int) -> None:
                    if total:
                        pct = 12 + int(38 * min(1.0, done / total))
                    else:
                        pct = min(50, 14 + int(done / (512 * 1024)))
                    await emit(progress, "download", pct, "📥 Загрузка аудиофайла…")

                async with SafeHttpClient(self.settings,
                                          allowed_hosts=provider.api_hosts) as client:
                    result = await client.download_to_file(
                        audio_source.url, dest,
                        headers=audio_source.headers or None,
                        max_bytes=self.settings.max_file_mb * 1024 * 1024,
                        expected_types=("audio/*", "video/mp4",
                                        "application/octet-stream",
                                        "binary/octet-stream"),
                        progress=on_bytes)
                content_type = result.content_type or content_type
                path = dest
            self._check_cancel(cancel)

            await emit(progress, "analyze", 55, "🔬 Проверка качества…")
            path, probe = await finalize(path, content_type=content_type,
                                         expected_format=audio_source.expected_format,
                                         job_id=job_id)
            analysis = await analyze_audio(
                path, expected_duration=candidate.duration,
                expected_title=candidate.title, expected_artist=candidate.artist,
                expected_album=candidate.album, deep=True)
            if analysis.category is QualityCategory.DAMAGED or not analysis.is_sendable:
                quarantine(path, job_id)
                reasons = "; ".join(i.message for i in analysis.issues if i.severity == "error")
                return DownloadOutcome(
                    ok=False, job_id=job_id, permission=permission,
                    official_url=official_url, error_code="DAMAGED",
                    error=f"Файл повреждён: {reasons or 'не прошёл проверку'}",
                    retryable=False)

            self._check_cancel(cancel)
            await emit(progress, "cover", 70, "🖼 Проверка обложки…")
            cover = None
            cover_path = None
            try:
                cover = await prepare_cover(candidate, provider, local_path=str(path))
                if cover and cover.candidate.is_valid and cover.accepted:
                    cover_path = store_cover_copy(
                        cover.candidate.data, cover.candidate.mime,
                        hashlib.sha256(cover.candidate.data).hexdigest())
            except Exception as exc:  # noqa: BLE001
                log.info("cover: %s", exc)

            self._check_cancel(cancel)
            await emit(progress, "metadata", 80, "🏷 Подготовка метаданных…")
            notes: list[str] = []
            try:
                fields = MetadataFields.from_candidate(candidate, probe.tags)
                embed = (cover is not None and cover.accepted
                         and cover.candidate.source not in ("", "embedded"))
                edit = write_metadata(
                    path, fields, cover=cover if embed else None,
                    cover_mime=(cover.candidate.mime if embed and cover else ""),
                    replace_cover=embed, overwrite=False)
                if edit.errors:
                    notes.append("Метаданные: " + "; ".join(edit.errors))
            except Exception as exc:  # noqa: BLE001
                notes.append(f"Метаданные не записаны: {type(exc).__name__}")
            probe.size = file_size(path)

            fmt = audio_format(path, probe.codec)
            media_kind = media_kind_for(fmt)
            if (candidate.extra or {}).get("force_document"):
                media_kind = MediaKind.DOCUMENT
            size = file_size(path)
            if size > self.settings.max_upload_mb * 1024 * 1024:
                return DownloadOutcome(
                    ok=False, job_id=job_id, permission=permission,
                    official_url=official_url, error_code="UPLOAD_TOO_LARGE",
                    error=(f"Файл {size // (1024 * 1024)} МБ больше лимита отправки "
                           f"{self.settings.max_upload_mb} МБ; открой официальную ссылку"),
                    retryable=False)

            sha = sha256_file(path)
            cache_key = source_key
            if session is not None:
                try:
                    from .. import repository
                    cached = await store_cached(
                        session, path, sha256=sha, file_format=fmt,
                        duration=probe.duration, media_kind=media_kind.value,
                        identity_key=candidate.identity_key, cache_key=cache_key,
                        quality_category=analysis.category.value,
                        bitrate_kbps=probe.bitrate_kbps,
                        sample_rate=probe.sample_rate,
                        title=candidate.title, artist=candidate.artist, move=True)
                    path = Path(cached.path)
                    moved_to_cache = True
                except Exception as exc:  # noqa: BLE001
                    log.warning("cache store: %s", exc)
                    notes.append("Не удалось сохранить в кэш")

            prepared = PreparedTrack(
                path=str(path), media_kind=media_kind, mime_type=mime_for(fmt),
                file_format=fmt, size=size, duration=probe.duration,
                title=candidate.title, artist=candidate.artist,
                album=candidate.album, candidate=candidate, audio=analysis,
                cover=cover, cover_path=cover_path, cache_key=source_key,
                sha256=sha, notes=notes)
            try:
                keep_workspace = Path(path).resolve().is_relative_to(workspace.resolve())
            except (ValueError, OSError):
                keep_workspace = False

            if session is not None:
                try:
                    from .. import repository
                    track = await repository.upsert_track(session, candidate)
                    await repository.record_history(
                        session, prepared, user_id=user_id, job_id=job_id,
                        track_id=track.id, permission=permission.status)
                    await repository.record_audio_report(session, prepared, track.id)
                    await repository.record_cover_report(session, prepared, track.id)
                    await session.commit()
                except Exception as exc:  # noqa: BLE001
                    log.warning("history: %s", exc)
                    try:
                        await session.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    prepared.notes.append("История не сохранена")

            await emit(progress, "done", 100, "✅ Готово")
            keep_cover = cover_path not in (None, "")
            return DownloadOutcome(ok=True, job_id=job_id, prepared=prepared,
                                   permission=permission)
        except JobCancelled:
            return DownloadOutcome(ok=False, job_id=job_id, permission=permission,
                                   official_url=official_url, error_code="CANCELLED",
                                   error="Отменено пользователем")
        except PermissionDenied as exc:
            return DownloadOutcome(ok=False, job_id=job_id, permission=permission,
                                   official_url=official_url, error_code=exc.code,
                                   error=str(exc))
        except (FileTooLarge, InvalidContent) as exc:
            return DownloadOutcome(ok=False, job_id=job_id, permission=permission,
                                   official_url=official_url, error_code=exc.code,
                                   error=str(exc), retryable=False)
        except MusicError as exc:
            return DownloadOutcome(ok=False, job_id=job_id, permission=permission,
                                   official_url=official_url, error_code=exc.code,
                                   error=str(exc), retryable=exc.retryable)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("download %s: неожиданная ошибка", job_id)
            return DownloadOutcome(ok=False, job_id=job_id, permission=permission,
                                   official_url=official_url, error_code="UNEXPECTED",
                                   error=f"{type(exc).__name__}: {exc}",
                                   retryable=True)
        finally:
            if cover_path and not keep_cover:
                try:
                    Path(cover_path).unlink(missing_ok=True)
                except OSError:
                    pass
            if not keep_workspace:
                cleanup_job_dir(job_id)

    async def _from_cache(self, cached, candidate: TrackCandidate,
                          source_key: str) -> PreparedTrack | None:
        if cached is None:
            return None
        path = Path(cached.path) if cached.path else None
        has_file = bool(path and path.exists() and path.stat().st_size == cached.size)
        if not has_file and not cached.telegram_file_id:
            return None
        fmt = cached.format or candidate.file_format or "mp3"
        limit = self.settings.max_upload_mb * 1024 * 1024
        if has_file and limit > 0 and path.stat().st_size > limit:
            # Локальный файл не пройдёт sendDocument/sendAudio: пусть пайплайн
            # заново скачает и вернёт понятную ошибку UPLOAD_TOO_LARGE.
            log.info("cache: %s больше лимита отправки %s МБ",
                     path, self.settings.max_upload_mb)
            return None
        if has_file:
            try:
                analysis = await analyze_audio(path, deep=False)
            except Exception:  # noqa: BLE001
                return None
            probe = analysis.probe
            stored = _category(cached.quality_category)
            if stored is not QualityCategory.UNKNOWN:
                analysis.category = stored
        else:
            probe = AudioProbe(container=fmt,
                               bitrate_kbps=cached.bitrate_kbps,
                               sample_rate=cached.sample_rate,
                               duration=cached.duration, size=cached.size,
                               has_audio=True)
            analysis = AudioAnalysis(probe=probe, category=_category(cached.quality_category),
                                     score=60)
        kind = MediaKind.DOCUMENT if cached.media_kind == "document" else MediaKind.AUDIO
        if (candidate.extra or {}).get("force_document"):
            kind = MediaKind.DOCUMENT
        return PreparedTrack(
            path=str(path) if has_file else "",
            media_kind=kind, mime_type=mime_for(fmt), file_format=fmt,
            size=cached.size, duration=cached.duration or probe.duration,
            title=candidate.title or cached.title, artist=candidate.artist or cached.artist,
            album=candidate.album, candidate=candidate, audio=analysis,
            cache_key=source_key, sha256=cached.sha256,
            telegram_file_id=cached.telegram_file_id,
            telegram_file_unique_id=cached.telegram_file_unique_id,
            cache_hit=True)

    @staticmethod
    def _check_cancel(cancel: Any | None) -> None:
        if cancel is not None and getattr(cancel, "is_set", lambda: False)():
            raise JobCancelled("Отменено", code="CANCELLED")


def _category(value: str) -> QualityCategory:
    try:
        return QualityCategory(value)
    except (ValueError, TypeError):
        return QualityCategory.UNKNOWN


manager = DownloadManager()

__all__ = ["DownloadManager", "manager", "emit", "ProgressFn"]
