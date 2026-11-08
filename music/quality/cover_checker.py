"""Cover Integrity: разрешение, чёткость, сжатие, повреждения, без апскейла."""
from __future__ import annotations

import io
import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageFile, UnidentifiedImageError

from ..domain import AudioIssue, CoverAnalysis, CoverCandidate

log = logging.getLogger("music.quality.cover")
ImageFile.LOAD_TRUNCATED_IMAGES = False
Image.MAX_IMAGE_PIXELS = 60_000_000


def has_embedded_cover(path: str | Path) -> bool:
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path)
        tags = getattr(audio, "tags", None)
        if tags is None:
            return False
        if hasattr(audio, "pictures"):
            return bool(getattr(audio, "pictures"))
        if "covr" in tags:
            return bool(tags.get("covr"))
        for key in tags.keys():
            if str(key).startswith("APIC"):
                return True
            if str(key).lower() == "metadata_block_picture":
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


def analyze_image(data: bytes, *, source_url: str = "",
                  source: str = "") -> CoverAnalysis:
    analysis = CoverAnalysis()
    if not data:
        analysis.add_issue("empty", "Обложка отсутствует", "error")
        return analysis
    try:
        with Image.open(io.BytesIO(data)) as check:
            check.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            width, height = image.size
            fmt = (image.format or "").upper()
            mime = Image.MIME.get(fmt, "image/jpeg")
            rgb = image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        analysis.add_issue("corrupted", f"Изображение повреждено: {exc}", "error")
        return analysis

    candidate = CoverCandidate(url=source_url, data=data, width=width, height=height,
                               format=fmt, mime=mime, size=len(data), source=source)
    analysis.candidate = candidate
    if width < 1 or height < 1:
        analysis.add_issue("empty", "Нулевой размер изображения", "error")
        return analysis

    thumb = rgb.copy()
    thumb.thumbnail((512, 512), Image.Resampling.LANCZOS)
    gray = np.asarray(thumb.convert("L"), dtype=np.float32)
    std = float(gray.std())
    if gray.shape[0] > 2 and gray.shape[1] > 2:
        lap = (-4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1]
               + gray[1:-1, :-2] + gray[1:-1, 2:])
        sharpness = float(lap.var())
    else:
        sharpness = 0.0
    if std < 3.0:
        # однотонная обложка не «размыта» — нет краёв, которые можно измерить
        sharpness = max(sharpness, 12.0)
    analysis.sharpness = round(sharpness, 3)
    analysis.blockiness = round(_blockiness(gray), 3)

    min_side = min(width, height)
    if min_side < 500:
        analysis.add_issue("small", f"Маленькая обложка {width}×{height}", "warning")
    if sharpness < 6.0:
        analysis.add_issue("blurry", "Низкая чёткость (размытие)", "warning")
    if analysis.blockiness > 1.35:
        analysis.add_issue("compression", "Сильные JPEG-артефакты сжатия", "warning")
    elif analysis.blockiness > 1.22:
        analysis.add_issue("compression_mild", "Заметное сжатие JPEG", "info")
    ratio = width / height if height else 0
    if ratio > 2.6 or ratio < 0.38:
        analysis.add_issue("aspect", "Необычное соотношение сторон", "info")
    if len(data) < 8 * 1024 and min_side >= 500:
        analysis.add_issue("tiny_file", "Подозрительно маленький файл для такого разрешения", "info")

    base = 100
    if min_side < 1400:
        base = 95 if min_side >= 1000 else (85 if min_side >= 800 else
                                            (72 if min_side >= 600 else
                                             (60 if min_side >= 500 else
                                              (38 if min_side >= 300 else 15))))
    score = base
    if sharpness < 10:
        score -= 25
    elif sharpness < 16:
        score -= 10
    if analysis.blockiness > 1.35:
        score -= 15
    elif analysis.blockiness > 1.2:
        score -= 6
    analysis.score = max(0, min(100, score))
    analysis.accepted = (min_side >= 500 and sharpness >= 6.0
                         and analysis.blockiness <= 1.35
                         and not any(i.severity == "error" for i in analysis.issues))
    return analysis


def _blockiness(gray: np.ndarray) -> float:
    """Отношение перепадов на границах JPEG-блоков 8×8 к обычным перепадам."""
    if gray.shape[0] < 16 or gray.shape[1] < 16:
        return 1.0
    dx = np.abs(np.diff(gray, axis=1))
    if dx.size == 0:
        return 1.0
    cols = np.arange(dx.shape[1])
    boundary = dx[:, (cols % 8) == 7]
    other = dx[:, (cols % 8) != 7]
    if boundary.size == 0 or other.size == 0:
        return 1.0
    b = float(boundary.mean())
    o = float(other.mean())
    if o < 1e-6:
        return 1.0
    vertical = b / o
    dy = np.abs(np.diff(gray, axis=0))
    rows = np.arange(dy.shape[0])
    b2 = float(dy[(rows % 8) == 7, :].mean()) if dy.size else 0.0
    o2 = float(dy[(rows % 8) != 7, :].mean()) if dy.size else 0.0
    horizontal = (b2 / o2) if o2 > 1e-6 else 1.0
    return max(1.0, min(vertical, horizontal, 4.0))


def choose_best(candidates: list[CoverAnalysis]) -> CoverAnalysis | None:
    valid = [c for c in candidates if c.candidate.is_valid]
    if not valid:
        return None
    accepted = [c for c in valid if c.accepted]
    pool = accepted or valid
    return max(pool, key=lambda c: (c.score, c.candidate.pixels, c.candidate.size))


def analyze_file(path: str | Path, *, source_url: str = "") -> CoverAnalysis:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        analysis = CoverAnalysis()
        analysis.add_issue("read", f"Не удалось прочитать обложку: {exc}", "error")
        return analysis
    return analyze_image(data, source_url=source_url, source=str(path))


__all__ = ["has_embedded_cover", "analyze_image", "analyze_file",
           "choose_best", "AudioIssue"]
