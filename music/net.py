"""Безопасные HTTP-запросы: SSRF-защита, редиректы, лимиты, Content-Type.

Все внешние загрузки музыкальной системы проходят через этот модуль:
- только http/https и порты 80/443;
- блокировка localhost/private/link-local/reserved/multicast адресов;
- проверка КАЖДОГО редиректа;
- жёсткий лимит размера и времени;
- проверка Content-Type и сигнатуры файла на уровне вызывающего кода.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx

from .config import MusicSettings, music_settings
from .errors import (DownloadError, FileTooLarge, InvalidContent, SSRFBlocked)

log = logging.getLogger("music.net")

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_SAFE_SCHEMES = {"http", "https"}
_BAD_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def _ip_is_forbidden(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
        # 100.64.0.0/10 (CGNAT) в Python 3.13 может не считаться private
        or (isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"))
        # 169.254.169.254 и прочие link-local уже покрыты, но оставим явно
        or ip == ipaddress.ip_address("169.254.169.254")
    )


def _normalize_host(host: str) -> str:
    return (host or "").strip().strip(".").lower()


def host_allowed(host: str, allowed: Iterable[str] | None) -> bool:
    if not allowed:
        return True
    host = _normalize_host(host)
    for item in allowed:
        item = _normalize_host(item)
        if host == item or host.endswith("." + item):
            return True
    return False


def validate_url_syntax(url: str, *, allowed_hosts: Iterable[str] | None = None,
                        allowed_ports: Sequence[int] = (80, 443)) -> str:
    """Синхронная проверка схемы/порта/учётных данных/allowlist."""
    raw = (url or "").strip()
    if not raw:
        raise SSRFBlocked("Пустой URL", code="URL_EMPTY")
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in _SAFE_SCHEMES:
        raise SSRFBlocked(f"Схема {scheme or '?'} запрещена", code="URL_SCHEME")
    if parts.username or parts.password:
        raise SSRFBlocked("URL с учётными данными запрещён", code="URL_CREDENTIALS")
    host = _normalize_host(parts.hostname or "")
    if not host:
        raise SSRFBlocked("URL без хоста", code="URL_HOST")
    if host in _BAD_HOSTS or host.endswith(".localhost"):
        raise SSRFBlocked("localhost запрещён", code="URL_LOCALHOST")
    try:
        port = parts.port
    except ValueError as exc:
        raise SSRFBlocked("Некорректный порт", code="URL_PORT") from exc
    if port is None:
        port = 443 if scheme == "https" else 80
    if port not in set(allowed_ports):
        raise SSRFBlocked(f"Порт {port} запрещён", code="URL_PORT")
    if not host_allowed(host, allowed_hosts):
        raise SSRFBlocked(f"Хост {host} не разрешён", code="URL_HOST_NOT_ALLOWED")
    return urlunsplit((scheme, parts.netloc, parts.path or "/", parts.query, ""))


async def _default_resolver(host: str, port: int) -> list[ipaddress._BaseAddress]:
    loop = asyncio.get_running_loop()
    infos = await loop.run_in_executor(
        None, lambda: socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
    out: list[ipaddress._BaseAddress] = []
    for info in infos:
        addr = info[4][0]
        try:
            out.append(ipaddress.ip_address(addr.split("%")[0]))
        except ValueError:
            continue
    return out


async def validate_url(url: str, *, allowed_hosts: Iterable[str] | None = None,
                       allowed_ports: Sequence[int] = (80, 443),
                       block_private: bool = True,
                       resolver: Callable[[str, int], Any] | None = None) -> str:
    """Полная проверка: синтаксис + DNS + запрет внутренних IP."""
    clean = validate_url_syntax(url, allowed_hosts=allowed_hosts,
                                allowed_ports=allowed_ports)
    if block_private:
        parts = urlsplit(clean)
        host = _normalize_host(parts.hostname or "")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        resolve = resolver or _default_resolver
        try:
            addresses = await resolve(host, port)
        except SSRFBlocked:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DownloadError(f"DNS не разрешил {host}: {exc}",
                                code="DNS_ERROR", retryable=True) from exc
        if not addresses:
            raise DownloadError(f"DNS не вернул адреса для {host}",
                                code="DNS_EMPTY", retryable=True)
        for ip in addresses:
            if _ip_is_forbidden(ip):
                raise SSRFBlocked(f"Адрес {ip} запрещён (внутренняя сеть)",
                                  code="URL_PRIVATE_IP")
    return clean


def sanitize_filename(name: str, *, fallback: str = "audio", max_len: int = 120) -> str:
    """Безопасное имя файла: без путей, управляющих символов и опасных расширений."""
    base = unquote((name or "").replace("\\", "/").split("/")[-1])
    base = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]+", "_", base)
    base = re.sub(r"\s+", " ", base).strip(" .")
    if not base:
        base = fallback
    stem, ext = os.path.splitext(base)
    ext = re.sub(r"[^A-Za-z0-9.]", "", ext)[:10].lower()
    stem = re.sub(r"[^\w\-. ()\[\]&']+", "_", stem, flags=re.UNICODE).strip(" ._") or fallback
    return (stem[:max_len] + ext)[:max_len + 10]


def filename_from_disposition(value: str | None) -> str:
    if not value:
        return ""
    m = re.search(r"filename\*=UTF-8''([^;]+)", value, re.I)
    if m:
        return sanitize_filename(unquote(m.group(1)))
    m = re.search(r'filename="?([^";]+)"?', value, re.I)
    return sanitize_filename(m.group(1)) if m else ""


@dataclass(slots=True)
class FetchResult:
    url: str
    status: int
    content_type: str
    data: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


class TokenBucket:
    """Простой async rate limiter (на пользователя/провайдера)."""

    def __init__(self, rate: int, per_seconds: float = 60.0):
        self.rate = max(1, int(rate))
        self.per = float(per_seconds)
        self._allowance = float(self.rate)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, amount: float = 1.0) -> None:
        async with self._lock:
            now = time.monotonic()
            self._allowance = min(self.rate,
                                  self._allowance + (now - self._last) * self.rate / self.per)
            self._last = now
            if self._allowance < amount:
                wait = (amount - self._allowance) * self.per / self.rate
                await asyncio.sleep(wait)
                self._allowance = 0.0
                self._last = time.monotonic()
            else:
                self._allowance -= amount


class SafeHttpClient:
    """httpx.AsyncClient с ручной обработкой редиректов и проверкой каждого хоста."""

    def __init__(self, settings: MusicSettings | None = None, *,
                 allowed_hosts: Iterable[str] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 resolver: Callable[[str, int], Any] | None = None,
                 proxy: str | None = None):
        self.settings = settings or music_settings
        self.allowed_hosts = list(allowed_hosts or self.settings.allow_hosts)
        self._resolver = resolver
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._proxy = proxy if proxy is not None else (self.settings.proxy_url or None)

    async def __aenter__(self) -> "SafeHttpClient":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def start(self) -> None:
        if self._client is None:
            kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(self.settings.download_timeout,
                                         connect=self.settings.connect_timeout),
                "follow_redirects": False,
                "trust_env": False,
                "headers": {"User-Agent": "VibeMusicBot/1.0 (+legal audio engine)"},
            }
            if self._transport is not None:
                kwargs["transport"] = self._transport
            if self._proxy:
                kwargs["proxy"] = self._proxy
            self._client = httpx.AsyncClient(**kwargs)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _check(self, url: str, allowed_hosts: Iterable[str] | None = None) -> str:
        return await validate_url(
            url,
            allowed_hosts=allowed_hosts if allowed_hosts is not None else self.allowed_hosts,
            allowed_ports=self.settings.allowed_ports,
            block_private=self.settings.block_private_ips,
            resolver=self._resolver,
        )

    async def request(self, method: str, url: str, *, allowed_hosts: Iterable[str] | None = None,
                      headers: dict[str, str] | None = None,
                      params: Any | None = None,
                      data: Any | None = None,
                      max_redirects: int = 5,
                      max_bytes: int | None = None,
                      expected_types: Sequence[str] | None = None) -> FetchResult:
        await self.start()
        assert self._client is not None
        current = await self._check(url, allowed_hosts)
        for _ in range(max_redirects + 1):
            try:
                async with self._client.stream(method, current, headers=headers,
                                               params=params, data=data) as resp:
                    if resp.status_code in _REDIRECT_CODES:
                        location = resp.headers.get("location")
                        if not location:
                            raise DownloadError("Редирект без Location", code="BAD_REDIRECT")
                        current = await self._check(urljoin(current, location), allowed_hosts)
                        params = None  # параметры уже вошли в Location
                        continue
                    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                    if expected_types and not _content_type_ok(ctype, expected_types):
                        raise InvalidContent(f"Content-Type {ctype or '?'} не ожидается",
                                             code="BAD_CONTENT_TYPE")
                    length = resp.headers.get("content-length")
                    limit = max_bytes or self.settings.max_file_mb * 1024 * 1024
                    if length and length.isdigit() and int(length) > limit:
                        raise FileTooLarge(f"Файл больше лимита ({length} байт)",
                                           code="FILE_TOO_LARGE")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes(64 * 1024):
                        total += len(chunk)
                        if total > limit:
                            raise FileTooLarge("Файл превысил лимит при чтении",
                                               code="FILE_TOO_LARGE")
                        chunks.append(chunk)
                    if resp.status_code >= 400:
                        raise DownloadError(f"HTTP {resp.status_code} от {urlsplit(current).hostname}",
                                            code=f"HTTP_{resp.status_code}",
                                            retryable=resp.status_code >= 500 or resp.status_code == 429)
                    return FetchResult(current, resp.status_code, ctype, b"".join(chunks),
                                       dict(resp.headers))
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError) as exc:
                raise DownloadError(f"Сеть: {type(exc).__name__}: {exc}",
                                    code="NETWORK", retryable=True) from exc
            except httpx.HTTPError as exc:
                raise DownloadError(f"HTTP: {type(exc).__name__}: {exc}",
                                    code="HTTP_ERROR", retryable=True) from exc
        raise DownloadError("Слишком много редиректов", code="TOO_MANY_REDIRECTS")

    async def get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("expected_types", ("application/json", "text/json", "text/plain"))
        kwargs.setdefault("max_bytes", 4 * 1024 * 1024)
        res = await self.request("GET", url, **kwargs)
        try:
            import json
            data = json.loads(res.data.decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            raise InvalidContent("Ответ не является JSON", code="BAD_JSON") from exc
        if not isinstance(data, dict):
            raise InvalidContent("JSON-ответ не объект", code="BAD_JSON")
        return data

    async def post_json(self, url: str, data: Any = None,
                        headers: dict[str, str] | None = None, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("expected_types", ("application/json", "text/json", "text/plain"))
        kwargs.setdefault("max_bytes", 1024 * 1024)
        res = await self.request("POST", url, data=data, headers=headers, **kwargs)
        try:
            import json
            parsed = json.loads(res.data.decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            raise InvalidContent("Ответ не является JSON", code="BAD_JSON") from exc
        if not isinstance(parsed, dict):
            raise InvalidContent("JSON-ответ не объект", code="BAD_JSON")
        return parsed

    async def download_to_file(self, url: str, dest: Path, *,
                               allowed_hosts: Iterable[str] | None = None,
                               headers: dict[str, str] | None = None,
                               max_bytes: int | None = None,
                               expected_types: Sequence[str] | None = None,
                               progress: Callable[[int, int], Any] | None = None) -> FetchResult:
        """Стриминг в файл `dest.part` с атомарным переименованием."""
        await self.start()
        assert self._client is not None
        current = await self._check(url, allowed_hosts)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        limit = max_bytes or self.settings.max_file_mb * 1024 * 1024
        for _ in range(6):
            total = 0
            try:
                async with self._client.stream("GET", current, headers=headers) as resp:
                    if resp.status_code in _REDIRECT_CODES:
                        location = resp.headers.get("location")
                        if not location:
                            raise DownloadError("Редирект без Location", code="BAD_REDIRECT")
                        current = await self._check(urljoin(current, location), allowed_hosts)
                        continue
                    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                    if resp.status_code >= 400:
                        raise DownloadError(f"HTTP {resp.status_code}",
                                            code=f"HTTP_{resp.status_code}",
                                            retryable=resp.status_code >= 500 or resp.status_code == 429)
                    if expected_types and not _content_type_ok(ctype, expected_types):
                        raise InvalidContent(f"Content-Type {ctype or '?'} не ожидается",
                                             code="BAD_CONTENT_TYPE")
                    length = resp.headers.get("content-length")
                    if length and length.isdigit() and int(length) > limit:
                        raise FileTooLarge(f"Файл больше лимита ({length} байт)",
                                           code="FILE_TOO_LARGE")
                    with part.open("wb") as fh:
                        async for chunk in resp.aiter_bytes(128 * 1024):
                            total += len(chunk)
                            if total > limit:
                                raise FileTooLarge("Файл превысил лимит", code="FILE_TOO_LARGE")
                            fh.write(chunk)
                            if progress:
                                result = progress(total, int(length) if length and length.isdigit() else 0)
                                if asyncio.iscoroutine(result):
                                    await result
                    if total == 0:
                        raise InvalidContent("Пустой файл", code="EMPTY_FILE")
                    part.replace(dest)
                    return FetchResult(current, resp.status_code, ctype, b"",
                                       dict(resp.headers))
            except BaseException:
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        raise DownloadError("Слишком много редиректов", code="TOO_MANY_REDIRECTS")


def _content_type_ok(ctype: str, expected: Sequence[str]) -> bool:
    if not ctype:
        return True
    for item in expected:
        item = item.lower()
        if item.endswith("/") and ctype.startswith(item):
            return True
        if ctype == item:
            return True
        if item == "audio/*" and ctype.startswith("audio/"):
            return True
        if item == "image/*" and ctype.startswith("image/"):
            return True
        if ctype == "application/octet-stream" and (
                item.startswith("audio/") or item.startswith("image/")
                or item == "application/octet-stream"):
            return True
    return False


_semaphores: dict[str, asyncio.Semaphore] = {}


def host_semaphore(host: str, limit: int) -> asyncio.Semaphore:
    key = f"{host}:{limit}"
    sem = _semaphores.get(key)
    if sem is None:
        sem = asyncio.Semaphore(max(1, limit))
        _semaphores[key] = sem
    return sem


__all__ = [
    "SafeHttpClient", "validate_url", "validate_url_syntax", "sanitize_filename",
    "filename_from_disposition", "TokenBucket", "FetchResult", "host_allowed",
    "host_semaphore",
]
