"""Cover Integrity: чёткость, сжатие, повреждения, миниатюры."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from music.processing.cover_processor import (make_background, make_thumbnail,
                                              store_cover_copy, upgrade_cover_url)
from music.quality.cover_checker import analyze_file, analyze_image, choose_best


def _noise_png(width: int, height: int, seed: int = 7) -> bytes:
    rng = np.random.default_rng(seed)
    data = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    image = Image.fromarray(data, mode="RGB")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _smooth_jpeg(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), (40, 80, 120))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    return out.getvalue()


class TestAnalyzeImage:
    def test_sharp_large_image_accepted(self):
        analysis = analyze_image(_noise_png(1200, 1200))
        assert analysis.candidate.is_valid is True
        assert analysis.candidate.width == 1200
        assert analysis.candidate.height == 1200
        assert analysis.candidate.format == "PNG"
        assert analysis.sharpness > 0
        assert analysis.accepted is True
        assert analysis.score >= 90

    def test_small_image_not_accepted(self):
        analysis = analyze_image(_noise_png(200, 200))
        assert analysis.accepted is False
        assert any(i.code == "small" for i in analysis.issues)
        assert analysis.score < 50

    def test_flat_image_not_penalized_as_blurry(self):
        analysis = analyze_image(_smooth_jpeg(800, 800))
        assert not any(i.code == "blurry" for i in analysis.issues)

    def test_corrupted_bytes(self):
        analysis = analyze_image(b"not an image at all")
        assert analysis.accepted is False
        assert any(i.code == "corrupted" and i.severity == "error"
                   for i in analysis.issues)

    def test_empty_bytes(self):
        analysis = analyze_image(b"")
        assert analysis.accepted is False
        assert any(i.code == "empty" for i in analysis.issues)

    def test_wide_aspect_info(self):
        analysis = analyze_image(_noise_png(1400, 400))
        assert any(i.code == "aspect" for i in analysis.issues)
        assert analysis.candidate.width == 1400

    def test_source_fields(self):
        analysis = analyze_image(_noise_png(800, 800),
                                 source_url="https://example.com/cover.png",
                                 source="network")
        assert analysis.candidate.url == "https://example.com/cover.png"
        assert analysis.candidate.source == "network"
        assert analysis.candidate.size > 0

    def test_file_missing(self, tmp_path: Path):
        analysis = analyze_file(tmp_path / "missing.jpg")
        assert analysis.accepted is False
        assert any(i.code == "read" for i in analysis.issues)

    def test_file_analysis(self, tmp_path: Path):
        path = tmp_path / "cover.png"
        path.write_bytes(_noise_png(1000, 1000))
        analysis = analyze_file(path, source_url="https://example.com/c.png")
        assert analysis.accepted is True
        assert analysis.candidate.source == str(path)


class TestChooseBest:
    def test_prefers_accepted_and_larger(self):
        small = analyze_image(_noise_png(600, 600, seed=1))
        large = analyze_image(_noise_png(1600, 1600, seed=2))
        best = choose_best([small, large])
        assert best is large

    def test_prefers_accepted_over_bigger_rejected(self):
        big_flat = analyze_image(_noise_png(2000, 2000, seed=3))
        # имитируем «не принята» у большой копии
        big_flat.accepted = False
        good = analyze_image(_noise_png(900, 900, seed=4))
        best = choose_best([big_flat, good])
        assert best is good

    def test_empty_list(self):
        assert choose_best([]) is None

    def test_invalid_candidates_ignored(self):
        assert choose_best([analyze_image(b"broken")]) is None


class TestCoverProcessing:
    def test_upgrade_soundcloud(self):
        url = "https://i1.sndcdn.com/artworks-abc-large.jpg"
        assert upgrade_cover_url(url) == "https://i1.sndcdn.com/artworks-abc-t500x500.jpg"

    def test_upgrade_jamendo(self):
        url = "https://imgjam.com/cover?size=300&id=1"
        assert "size=1200" in upgrade_cover_url(url)

    def test_upgrade_jamendo_without_size(self):
        url = "https://imgjam.com/cover?id=1"
        assert upgrade_cover_url(url).endswith("size=1200")

    def test_upgrade_bandcamp(self):
        url = "https://f4.bcbits.com/img/a1234567890_16.jpg"
        assert upgrade_cover_url(url).endswith("_10.jpg")

    def test_upgrade_keeps_unknown(self):
        url = "https://example.com/cover.jpg"
        assert upgrade_cover_url(url) == url
        assert upgrade_cover_url("") == ""

    def test_thumbnail_is_square_jpeg(self):
        source = Image.new("RGB", (1000, 700), (200, 30, 30))
        out = io.BytesIO()
        source.save(out, format="PNG")
        thumb = make_thumbnail(out.getvalue())
        assert thumb is not None
        with Image.open(io.BytesIO(thumb)) as image:
            assert image.format == "JPEG"
            assert image.width == image.height
            assert image.width <= 320
        assert len(thumb) <= 200 * 1024

    def test_thumbnail_invalid_data(self):
        assert make_thumbnail(b"broken") is None

    def test_background_is_blurred_copy(self):
        original = _noise_png(900, 900)
        background = make_background(original)
        assert background is not None
        assert background != original
        with Image.open(io.BytesIO(background)) as image:
            assert image.format == "JPEG"
            assert max(image.size) <= 640

    def test_store_cover_copy_keeps_bytes(self, temp_music_dir: Path):
        data = _noise_png(800, 800)
        path = store_cover_copy(data, "image/png", "abcdef123456")
        assert path is not None
        assert Path(path).read_bytes() == data
        assert Path(path).suffix == ".png"

    def test_store_cover_copy_idempotent(self, temp_music_dir: Path):
        data = _noise_png(600, 600)
        first = store_cover_copy(data, "image/png", "key12345")
        second = store_cover_copy(data, "image/png", "key12345")
        assert first == second
