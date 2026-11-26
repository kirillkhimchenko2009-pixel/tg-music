"""Категории качества и итоговая оценка файла."""
from __future__ import annotations

from ..domain import AudioAnalysis, AudioIssue, AudioProbe
from ..enums import QualityCategory
from .bitrate_checker import bitrate_tier, is_lossless, is_lossy
from .clipping_detector import ClipStats
from .metadata_checker import MetadataReport


def structural_issues(probe: AudioProbe, min_duration: int = 8) -> list[AudioIssue]:
    issues: list[AudioIssue] = []
    if probe.ffprobe_error:
        issues.append(AudioIssue("probe_error",
                                 f"Контейнер повреждён: {probe.ffprobe_error[:200]}", "error"))
    if not probe.has_audio:
        issues.append(AudioIssue("no_audio", "В файле нет аудиопотока", "error"))
    if probe.duration <= 0:
        issues.append(AudioIssue("no_duration", "Не удалось определить длительность", "error"))
    elif probe.duration < 3:
        issues.append(AudioIssue("too_short", "Файл короче 3 секунд", "error"))
    elif probe.duration < min_duration:
        issues.append(AudioIssue("short", f"Подозрительно короткий файл ({probe.duration:.1f}с)",
                                 "warning"))
    for warning in probe.warnings:
        issues.append(AudioIssue("container_warning", warning, "warning"))
    if probe.has_video:
        issues.append(AudioIssue("video_stream", "В контейнере есть видеопоток", "info"))
    if probe.size and probe.duration > 30 and probe.size < probe.duration * 2000:
        issues.append(AudioIssue("tiny_file", "Слишком маленький размер для такой длительности",
                                 "warning"))
    return issues


def categorize(probe: AudioProbe, clip: ClipStats, *,
               bandwidth_hz: int | None = None, likely_transcoded: bool = False,
               transcode_reason: str = "",
               metadata: MetadataReport | None = None,
               silence_total: float = 0.0, expected_duration: int = 0,
               min_duration: int = 8) -> tuple[QualityCategory, int, list[AudioIssue]]:
    issues = structural_issues(probe, min_duration)
    errors = any(i.severity == "error" for i in issues)

    if clip.decode_failed:
        issues.append(AudioIssue("decode_failed", "Аудиопоток не декодируется", "error"))
        errors = True
    if clip.clipping:
        severity = "warning"
        if clip.clipped_ratio > 0.002 or clip.long_runs > 200:
            severity = "error"
            errors = True
        issues.append(AudioIssue("clipping",
                                 f"Клиппинг: {clip.clipped_samples} сэмплов, "
                                 f"{clip.long_runs} плоских вершин", severity))
    if silence_total > 0 and probe.duration:
        ratio = silence_total / probe.duration
        if silence_total >= 20 or ratio > 0.15:
            issues.append(AudioIssue("silence",
                                     f"Тишина {silence_total:.0f}с "
                                     f"({ratio * 100:.0f}% длительности)", "warning"))
    if likely_transcoded:
        issues.append(AudioIssue("transcode", transcode_reason or
                                 "Возможное повторное перекодирование", "warning"))
    if probe.sample_rate and probe.sample_rate < 32000 and not errors:
        issues.append(AudioIssue("low_sample_rate",
                                 f"Низкая частота дискретизации {probe.sample_rate} Гц",
                                 "warning"))
    if metadata:
        for text in metadata.issues:
            severity = "warning" if metadata.suspicious else "info"
            issues.append(AudioIssue("metadata", text, severity))
        if expected_duration and probe.duration:
            diff = abs(probe.duration - float(expected_duration))
            if expected_duration >= 90 and probe.duration < min(90.0, expected_duration * 0.8):
                # превью-подпись: крошечный файл против полноценного трека —
                # это не «ремикс покороче», это мусор; ошибка = DAMAGED
                issues.append(AudioIssue(
                    "preview_like",
                    f"Похоже на превью: {probe.duration:.0f}с вместо "
                    f"{expected_duration}с",
                    "error"))
            elif diff > max(10.0, expected_duration * 0.12):
                issues.append(AudioIssue(
                    "duration_mismatch",
                    f"Длительность {probe.duration:.0f}с ≠ ожидаемой {expected_duration}с",
                    "warning"))

    if errors:
        category = QualityCategory.DAMAGED
    elif is_lossless(probe.codec) or probe.is_lossless:
        if (probe.sample_rate and probe.sample_rate < 32000) or (
                probe.bit_depth and probe.bit_depth < 16):
            category = QualityCategory.STANDARD_QUALITY
        elif likely_transcoded:
            category = QualityCategory.HIGH_QUALITY
        else:
            category = QualityCategory.LOSSLESS_SOURCE
    elif is_lossy(probe.codec):
        tier = bitrate_tier(probe.codec, probe.bitrate_kbps)
        if tier == "high":
            category = (QualityCategory.STANDARD_QUALITY if likely_transcoded
                        else QualityCategory.HIGH_QUALITY)
        elif tier == "standard":
            category = (QualityCategory.LOW_QUALITY if likely_transcoded
                        else QualityCategory.STANDARD_QUALITY)
        elif tier == "low":
            category = QualityCategory.LOW_QUALITY
        else:
            category = QualityCategory.UNKNOWN
    else:
        category = QualityCategory.UNKNOWN

    base = {
        QualityCategory.LOSSLESS_SOURCE: 96,
        QualityCategory.HIGH_QUALITY: 82,
        QualityCategory.STANDARD_QUALITY: 62,
        QualityCategory.LOW_QUALITY: 32,
        QualityCategory.DAMAGED: 5,
        QualityCategory.UNKNOWN: 45,
    }[category]
    score = base
    if clip.clipping:
        score -= 18 if any(i.severity == "error" for i in issues if i.code == "clipping") else 8
    if silence_total > 0 and probe.duration and silence_total / probe.duration > 0.15:
        score -= 5
    if likely_transcoded:
        score -= 7
    if metadata:
        score -= min(6, len(metadata.issues))
        if metadata.complete:
            score += 2
    if probe.channels == 1 and category in (QualityCategory.HIGH_QUALITY,
                                           QualityCategory.LOSSLESS_SOURCE):
        score -= 2
    if category is QualityCategory.DAMAGED:
        score = min(score, 10)
    return category, max(0, min(100, score)), issues


def fill_analysis(analysis: AudioAnalysis, *, clip: ClipStats,
                  metadata: MetadataReport | None,
                  silence_leading: float = 0.0, silence_trailing: float = 0.0,
                  silence_total: float = 0.0, bandwidth_hz: int | None = None,
                  likely_transcoded: bool = False, transcode_reason: str = "",
                  expected_duration: int = 0, min_duration: int = 8) -> AudioAnalysis:
    category, score, issues = categorize(
        analysis.probe, clip, bandwidth_hz=bandwidth_hz,
        likely_transcoded=likely_transcoded, transcode_reason=transcode_reason,
        metadata=metadata, silence_total=silence_total,
        expected_duration=expected_duration, min_duration=min_duration)
    analysis.category = category
    analysis.score = score
    analysis.issues = issues
    analysis.clipping = clip.clipping
    analysis.clipped_samples = clip.clipped_samples
    analysis.clipped_ratio = clip.clipped_ratio
    analysis.peak_dbfs = clip.peak_dbfs
    analysis.rms_dbfs = clip.rms_dbfs
    analysis.silence_leading = round(silence_leading, 2)
    analysis.silence_trailing = round(silence_trailing, 2)
    analysis.silence_total = round(silence_total, 2)
    analysis.bandwidth_hz = bandwidth_hz
    analysis.likely_transcoded = likely_transcoded
    return analysis


__all__ = ["structural_issues", "categorize", "fill_analysis"]
