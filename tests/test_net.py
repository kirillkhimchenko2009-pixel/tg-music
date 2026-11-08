"""SSRF-защита, безопасные HTTP-запросы, имена файлов, rate limiter."""
from __future__ import annotations

import ipaddress
from pathlib import Path

import httpx
import pytest

from music.config import MusicSettings
from music.errors import (DownloadError, FileTooLarge, InvalidContent,
                          SSRFBlocked)
from music.net import (SafeHttpClient, TokenBucket, filename_from_disposition,
                       host_allowed, host_semaphore, sanitize_filename,
                       validate_url, validate_url_syntax)


PUBLIC_IP = ipaddress.ip_address("93.184.216.34")


async def public_resolver(host: str, port: int):
    """Не ходим в DNS: example-хосты считаются публичными."""
    if host in ("127.0.0.1", "::1", "localhost"):
        return [ipaddress.ip_address(host)]
    if host == "10.0.0.1":
        return [ipaddress.ip_address(host)]
    return [PUBLIC_IP]


def _settings(**kwargs) -> MusicSettings:
    base = dict(allow_hosts=["example.com", "cdn.example.com"],
                block_private_ips=True)
    base.update(kwargs)
    return MusicSettings(**base)


class TestValidateUrlSyntax:
    @pytest.mark.parametrize("url,code", [
        ("", "URL_EMPTY"),
        ("ftp://example.com/file.mp3", "URL_SCHEME"),
        ("file:///etc/passwd", "URL_SCHEME"),
        ("javascript:alert(1)", "URL_SCHEME"),
        ("https://user:pass@example.com/x", "URL_CREDENTIALS"),
        ("https:///no-host", "URL_HOST"),
        ("http://localhost/x", "URL_LOCALHOST"),
        ("http://app.localhost/x", "URL_LOCALHOST"),
        ("http://example.com:8080/x", "URL_PORT"),
        ("https://evil.com/x", "URL_HOST_NOT_ALLOWED"),
    ])
    def test_rejects_bad_urls(self, url, code):
        with pytest.raises(SSRFBlocked) as info:
            validate_url_syntax(url, allowed_hosts=["example.com"])
        assert info.value.code == code

    def test_accepts_plain_https(self):
        clean = validate_url_syntax("https://example.com/a/b?x=1#frag")
        assert clean == "https://example.com/a/b?x=1"

    def test_allows_subdomains(self):
        assert host_allowed("cdn.example.com", ["example.com"]) is True
        assert host_allowed("notexample.com", ["example.com"]) is False

    def test_custom_port_allowed_when_configured(self):
        clean = validate_url_syntax("http://example.com:8080/x",
                                    allowed_hosts=["example.com"],
                                    allowed_ports=(80, 443, 8080))
        assert clean.startswith("http://example.com:8080")


class TestValidateUrlDns:
    async def test_public_ip_allowed(self):
        url = await validate_url("https://example.com/file", resolver=public_resolver)
        assert url == "https://example.com/file"

    @pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "::1",
                                    "169.254.169.254", "192.168.1.1"])
    async def test_private_ip_blocked(self, ip):
        async def resolver(host, port):
            return [ipaddress.ip_address(ip)]

        with pytest.raises(SSRFBlocked) as info:
            await validate_url("https://example.com/x", resolver=resolver)
        assert info.value.code == "URL_PRIVATE_IP"

    async def test_dns_error_is_retryable(self):
        async def resolver(host, port):
            raise OSError("no dns")

        with pytest.raises(DownloadError) as info:
            await validate_url("https://example.com/x", resolver=resolver)
        assert info.value.code == "DNS_ERROR"
        assert info.value.retryable is True

    async def test_private_ips_can_be_disabled(self):
        async def resolver(host, port):
            return [ipaddress.ip_address("127.0.0.1")]

        url = await validate_url("https://example.com/x", block_private=False,
                                 resolver=resolver)
        assert url


class TestSanitizeFilename:
    @pytest.mark.parametrize("raw,expected", [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\cmd.exe", "cmd.exe"),
        ("C:\\music\\track.mp3", "track.mp3"),
        ("", "audio"),
        ("   ", "audio"),
        ("...", "audio"),
    ])
    def test_paths(self, raw, expected):
        assert sanitize_filename(raw) == expected

    def test_control_chars(self):
        assert sanitize_filename("a\x00b\x1fc.mp3") == "a_b_c.mp3"

    def test_long_name(self):
        assert len(sanitize_filename("x" * 500, max_len=60)) <= 70

    def test_keeps_extension(self):
        assert sanitize_filename("My Song.MP3").endswith(".mp3")

    def test_disposition_plain(self):
        assert filename_from_disposition(
            'attachment; filename="My Song.mp3"') == "My Song.mp3"

    def test_disposition_rfc5987(self):
        assert filename_from_disposition(
            "attachment; filename*=UTF-8''%D0%A2%D1%80%D0%B5%D0%BA.mp3") == "Трек.mp3"

    def test_disposition_missing(self):
        assert filename_from_disposition(None) == ""


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


class TestSafeHttpClient:
    async def test_follows_redirect_and_checks_each_host(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                return httpx.Response(302, headers={"location":
                                                    "https://cdn.example.com/file.mp3"})
            return httpx.Response(200, content=b"ID3audio",
                                  headers={"content-type": "audio/mpeg"})

        async with SafeHttpClient(_settings(), transport=_transport(handler),
                                  resolver=public_resolver, proxy="") as client:
            result = await client.request("GET", "https://example.com/start",
                                          expected_types=("audio/*",))
        assert result.url == "https://cdn.example.com/file.mp3"
        assert result.data == b"ID3audio"

    async def test_redirect_to_private_ip_blocked(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

        async with SafeHttpClient(_settings(), transport=_transport(handler),
                                  resolver=public_resolver, proxy="") as client:
            with pytest.raises(SSRFBlocked):
                await client.request("GET", "https://example.com/start")

    async def test_bad_content_type(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>",
                                  headers={"content-type": "text/html"})

        async with SafeHttpClient(_settings(), transport=_transport(handler),
                                  resolver=public_resolver, proxy="") as client:
            with pytest.raises(InvalidContent):
                await client.request("GET", "https://example.com/x",
                                     expected_types=("audio/*",))

    async def test_content_length_limit(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"x" * 100,
                                  headers={"content-type": "audio/mpeg",
                                           "content-length": "100"})

        async with SafeHttpClient(_settings(), transport=_transport(handler),
                                  resolver=public_resolver, proxy="") as client:
            with pytest.raises(FileTooLarge):
                await client.request("GET", "https://example.com/x", max_bytes=10)

    async def test_http_error_is_retryable_for_5xx(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b"down")

        async with SafeHttpClient(_settings(), transport=_transport(handler),
                                  resolver=public_resolver, proxy="") as client:
            with pytest.raises(DownloadError) as info:
                await client.request("GET", "https://example.com/x")
        assert info.value.code == "HTTP_503"
        assert info.value.retryable is True

    async def test_json_parsing(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b'{"ok": true}',
                                  headers={"content-type": "application/json"})

        async with SafeHttpClient(_settings(), transport=_transport(handler),
                                  resolver=public_resolver, proxy="") as client:
            assert await client.get_json("https://example.com/api") == {"ok": True}

    async def test_bad_json(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not-json",
                                  headers={"content-type": "application/json"})

        async with SafeHttpClient(_settings(), transport=_transport(handler),
                                  resolver=public_resolver, proxy="") as client:
            with pytest.raises(InvalidContent):
                await client.get_json("https://example.com/api")

    async def test_download_to_file(self, tmp_path: Path):
        payload = b"ID3" + b"a" * 5000
        progress: list[tuple[int, int]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=payload,
                                  headers={"content-type": "audio/mpeg",
                                           "content-length": str(len(payload))})

        dest = tmp_path / "out.mp3"
        async with SafeHttpClient(_settings(), transport=_transport(handler),
                                  resolver=public_resolver, proxy="") as client:
            await client.download_to_file(
                "https://example.com/file.mp3", dest,
                progress=lambda done, total: progress.append((done, total)))
        assert dest.read_bytes() == payload
        assert not dest.with_name(dest.name + ".part").exists()
        assert progress and progress[-1][0] == len(payload)

    async def test_download_empty_file_rejected(self, tmp_path: Path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"",
                                  headers={"content-type": "audio/mpeg"})

        async with SafeHttpClient(_settings(), transport=_transport(handler),
                                  resolver=public_resolver, proxy="") as client:
            with pytest.raises(InvalidContent):
                await client.download_to_file("https://example.com/empty.mp3",
                                              tmp_path / "empty.mp3")
        assert not (tmp_path / "empty.mp3").exists()


class TestTokenBucket:
    async def test_immediate_when_budget_available(self):
        bucket = TokenBucket(rate=10, per_seconds=60)
        for _ in range(10):
            await bucket.acquire()

    async def test_semaphore_singleton(self):
        assert host_semaphore("example.com", 2) is host_semaphore("example.com", 2)
        assert host_semaphore("example.com", 2) is not host_semaphore("example.com", 3)
