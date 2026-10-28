"""Pinggy free tunnel: reverse-SSH через INCY, автоэкспорт URL мини-аппа.

- auth: user 'free' / password 'free'
- request_port_forward('localhost', 0, handler) -> сервер открывает слушателя
- handler прокачивает входящие каналы в localhost:8000 (FastAPI)
- URL (free.pinggy.net) пишется в data/tunnel_url.txt -> бот сам
  переустанавливает menu button (services.webapp_state.watch_webapp_url)
- free-сессии живут ~60 мин: рестарт каждые ~55 мин, бот подхватит новый URL.
Требование: сервер FastAPI запущен на 127.0.0.1:8000.
"""
import logging
import re
import socket
import threading
import time
from pathlib import Path

import paramiko

PROXY = ("127.0.0.1", 10809)   # INCY HTTP
TARGET = ("a.pinggy.io", 443)
LOCAL = ("127.0.0.1", 8000)
URL_FILE = Path(__file__).resolve().parent.parent / "data" / "tunnel_url.txt"
WEBAPP_FILE = Path(__file__).resolve().parent.parent / "data" / "webapp_url.txt"
RESTART_EVERY = 55 * 60   # сек до планового переподъёма сессии
RETRY_AFTER_FAIL = 15     # сек после аварии

URL_RE = re.compile(r"https://[a-zA-Z0-9.-]+\.free\.pinggy\.net")

log = logging.getLogger("pinggy")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] pinggy: %(message)s")


def export_url(url: str) -> None:
    """Пишем ОБА файла: whichever tunnel wrote last owns the menu button
    (watcher prefers webapp_url.txt = полный URL, тут добавляем /v2/)."""
    URL_FILE.parent.mkdir(exist_ok=True)
    if not URL_FILE.exists() or URL_FILE.read_text(encoding="utf-8").strip() != url:
        URL_FILE.write_text(url, encoding="utf-8")
        WEBAPP_FILE.write_text(url + "/v2/", encoding="utf-8")
        log.info("URL записан: %s", url)


def proxy_socket() -> socket.socket:
    s = socket.create_connection(PROXY, 10)
    s.sendall(f"CONNECT {TARGET[0]}:{TARGET[1]} HTTP/1.1\r\n"
              f"Host: {TARGET[0]}:{TARGET[1]}\r\n\r\n".encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = s.recv(4096)
        if not d:
            raise ConnectionError("прокси закрыл CONNECT")
        buf += d
    if b" 200" not in buf.split(b"\r\n")[0]:
        raise ConnectionError(f"CONNECT отклонён: {buf.split(b'\r\n')[0]!r}")
    return s


def splice_to_local(ch: paramiko.Channel) -> None:
    """Прокачка ssh-канала <-> локальный сокет 8000."""
    try:
        local = socket.create_connection(LOCAL, 5)
    except OSError as e:
        log.warning("локальный dial %s упал: %s", LOCAL, e)
        ch.close()
        return

    def pump_out():
        try:
            while True:
                d = ch.recv(16384)
                if not d:
                    break
                local.sendall(d)
        except (OSError, paramiko.SSHException):
            pass
        try:
            local.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    threading.Thread(target=pump_out, daemon=True).start()
    try:
        while True:
            d = local.recv(16384)
            if not d:
                break
            ch.sendall(d)
    except (OSError, paramiko.SSHException):
        pass
    try:
        ch.close()
    except Exception:
        pass
    try:
        local.close()
    except Exception:
        pass


def on_forwarded(ch: paramiko.Channel, origin, server) -> None:
    threading.Thread(target=splice_to_local, args=(ch,), daemon=True).start()


def run_session() -> None:
    """Одна сессия туннеля; живёт до планового/аварийного конца."""
    t = paramiko.Transport(proxy_socket())
    try:
        t.connect(username="free", password="free")
        log.info("auth ok")

        shell = t.open_session()
        shell.get_pty("dumb", 200, 50)
        shell.invoke_shell()

        assigned = t.request_port_forward("localhost", 0, handler=on_forwarded)
        log.info("tcpip-forward: assigned port %s", assigned)

        url = None
        deadline = time.time() + 25
        buf = b""
        while time.time() < deadline:
            if shell.recv_ready():
                buf += shell.recv(8192)
            m = URL_RE.search(buf.decode("utf-8", "replace"))
            if m:
                url = m.group(0).rstrip("/")
                break
            time.sleep(0.4)
        if not url:
            log.warning("URL не получен; хвост баннера: %r", buf[-300:])
            return
        export_url(url)
        log.info("Туннель работает: %s (порт %s)", url, assigned)

        end = time.time() + RESTART_EVERY
        while time.time() < end:
            if not t.is_active():
                log.warning("transport умер досрочно — пересоздаём")
                return
            time.sleep(5)
        log.info("регламентный рестарт (free ~60 мин)")
    finally:
        try:
            t.close()
        except Exception:
            pass


def main() -> None:
    while True:
        try:
            run_session()
        except KeyboardInterrupt:
            return
        except Exception:
            log.exception("сессия упала — повтор через %d c", RETRY_AFTER_FAIL)
        time.sleep(RETRY_AFTER_FAIL)


if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
    from core.single_instance import acquire
    acquire("tunnel")
    main()
