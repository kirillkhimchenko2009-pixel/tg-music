"""Работа с временными файлами: изоляция, хеши, очистка, сигнатуры."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest

from music.config import music_settings
from music.errors import ProcessingError
from music.storage import (cleanup_job_dir, cleanup_temp, detect_audio_extension,
                           ensure_not_executable, file_size, guess_extension,
                           new_job_id, quarantine, safe_path, sha256_bytes,
                           sha256_file, temp_file, workspace_for)


class TestWorkspace:
    def test_new_job_id_is_hex(self):
        job_id = new_job_id()
        assert len(job_id) == 32
        int(job_id, 16)

    def test_workspace_inside_tmp(self, temp_music_dir: Path):
        job_id = new_job_id()
        path = workspace_for(job_id)
        assert path.exists()
        assert path.parent == music_settings.tmp_dir
        assert path.name == job_id

    @pytest.mark.parametrize("job_id", ["", "../etc", "short", "ZZZZZZZZ", "a/b" * 8])
    def test_bad_job_id(self, temp_music_dir: Path, job_id):
        with pytest.raises(ProcessingError):
            workspace_for(job_id)

    def test_temp_file_has_suffix(self, temp_music_dir: Path):
        path = temp_file(new_job_id(), ".mp3", "My Song")
        assert path.suffix == ".mp3"
        assert path.parent.exists()
        assert not path.exists()  # путь выдаётся заранее, файл создаёт загрузчик

    def test_temp_file_sanitizes_suffix(self, temp_music_dir: Path):
        path = temp_file(new_job_id(), "../exe", "x")
        assert path.suffix in (".bin", ".exe")


class TestSafePath:
    def test_keeps_inside_base(self, tmp_path: Path):
        base = tmp_path / "base"
        base.mkdir()
        result = safe_path(base, "../../etc/passwd")
        assert result.parent == base
        assert result.name == "passwd"

    def test_absolute_path_inside_base(self, tmp_path: Path):
        base = tmp_path / "base"
        base.mkdir()
        result = safe_path(base, str(tmp_path / "other" / "file.mp3"))
        assert result.parent == base


class TestHashesAndSizes:
    def test_sha256_bytes(self):
        assert sha256_bytes(b"music") == hashlib.sha256(b"music").hexdigest()

    def test_sha256_file(self, tmp_path: Path):
        path = tmp_path / "file.bin"
        path.write_bytes(b"x" * 10_000)
        assert sha256_file(path) == hashlib.sha256(b"x" * 10_000).hexdigest()

    def test_file_size_missing(self, tmp_path: Path):
        assert file_size(tmp_path / "none.bin") == 0

    def test_sha256_missing(self, tmp_path: Path):
        with pytest.raises(OSError):
            sha256_file(tmp_path / "none.bin")


class TestCleanup:
    def test_cleanup_job_dir(self, temp_music_dir: Path):
        job_id = new_job_id()
        workspace = workspace_for(job_id)
        (workspace / "a.bin").write_bytes(b"x")
        cleanup_job_dir(job_id)
        assert not workspace.exists()

    def test_cleanup_temp_removes_old_only(self, temp_music_dir: Path):
        old = music_settings.tmp_dir / (new_job_id())
        old.mkdir()
        (old / "x.bin").write_bytes(b"x")
        fresh = music_settings.tmp_dir / (new_job_id())
        fresh.mkdir()
        stamp = time.time() - 10 * 3600
        os.utime(old, (stamp, stamp))
        removed = cleanup_temp(ttl_hours=1)
        assert removed >= 1
        assert not old.exists()
        assert fresh.exists()

    def test_cleanup_temp_missing_dir(self, temp_music_dir: Path):
        import shutil
        shutil.rmtree(music_settings.tmp_dir)
        assert cleanup_temp() == 0


class TestSignatures:
    @pytest.mark.parametrize("head,expected", [
        (b"ID3\x04\x00\x00", ".mp3"),
        (b"\xff\xfb\x90\x00", ".mp3"),
        (b"fLaC\x00\x00\x00", ".flac"),
        (b"OggS\x00\x02\x00\x00", ".ogg"),
        (b"OggS\x00\x02OpusHead", ".opus"),
        (b"RIFF\x00\x00\x00\x00WAVE", ".wav"),
        (b"\x00\x00\x00\x20ftypM4A ", ".m4a"),
        (b"unknown-signature", ".bin"),
    ])
    def test_detect_audio_extension(self, tmp_path: Path, head, expected):
        path = tmp_path / "file.bin"
        path.write_bytes(head + b"\x00" * 32)
        assert detect_audio_extension(path) == expected

    @pytest.mark.parametrize("ctype,url,expected", [
        ("audio/mpeg", "", ".mp3"),
        ("audio/flac", "", ".flac"),
        ("audio/x-m4a", "", ".m4a"),
        ("application/octet-stream", "https://x/file.opus?token=1", ".opus"),
        ("application/octet-stream", "", ".bin"),
        ("", "https://x/file.JPEG", ".jpg"),
    ])
    def test_guess_extension(self, ctype, url, expected):
        assert guess_extension(ctype, url) == expected

    @pytest.mark.parametrize("name,expected", [
        ("track.mp3", "track.mp3"),
        ("virus.exe", "virus.bin"),
        ("script.PS1", "script.bin"),
        ("archive.rar", "archive.rar"),
        ("noext", "noext"),
    ])
    def test_ensure_not_executable(self, name, expected):
        assert ensure_not_executable(name) == expected


class TestQuarantine:
    def test_moves_file(self, temp_music_dir: Path, tmp_path: Path):
        source = tmp_path / "broken.mp3"
        source.write_bytes(b"broken")
        dest = quarantine(source, new_job_id())
        assert dest is not None
        assert dest.exists()
        assert not source.exists()
        assert dest.parent == music_settings.quarantine_dir

    def test_missing_file_returns_none(self, temp_music_dir: Path, tmp_path: Path):
        assert quarantine(tmp_path / "missing.mp3", new_job_id()) is None
