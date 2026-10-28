# -*- coding: utf-8 -*-
"""Локальный TCP-релей для Postgres: 127.0.0.1:15432 -> (CONNECT-прокси 10809) -> Neon:5432.

Нужен, потому что DPI этого канала рвёт прямые TLS-потоки к нестандартным портам
(5432), а трафик прокси — нет. Использование:
    python tools/pg_relay.py              # слушает 15432, TARGET ниже
    DATABASE_URL=postgresql://user:pass@127.0.0.1:15432/neondb?sslmode=require
Облако (Render) ходит к Neon напрямую — релей нужен только локально.
"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LISTEN_PORT = int(os.getenv("PG_RELAY_PORT", "15432"))
PROXY = ("127.0.0.1", int(os.getenv("PG_RELAY_PROXY_PORT", "10809")))
TARGET_HOST = os.getenv("PG_RELAY_TARGET", "")
TARGET_PORT = 5432


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(c_reader: asyncio.StreamReader, c_writer: asyncio.StreamWriter) -> None:
    p_reader = p_writer = None
    try:
        p_reader, p_writer = await asyncio.open_connection(*PROXY)
        req = (f"CONNECT {TARGET_HOST}:{TARGET_PORT} HTTP/1.1\r\n"
               f"Host: {TARGET_HOST}:{TARGET_PORT}\r\n\r\n")
        p_writer.write(req.encode())
        await p_writer.drain()
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = await p_reader.read(4096)
            if not chunk:
                raise RuntimeError("proxy closed: " + buf.decode(errors="replace")[:120])
            buf += chunk
        status = buf.split(b"\r\n", 1)[0]
        if b" 200" not in status:
            raise RuntimeError("proxy refused: " + status.decode(errors="replace"))
        await asyncio.gather(pipe(p_reader, c_writer), pipe(c_reader, p_writer))
    except Exception as e:
        print(f"[relay] drop: {e}", flush=True)
        try:
            c_writer.close()
        except Exception:
            pass
    finally:
        if p_writer is not None:
            try:
                p_writer.close()
            except Exception:
                pass


async def main() -> None:
    srv = await asyncio.start_server(handle, "127.0.0.1", LISTEN_PORT)
    print(f"[relay] 127.0.0.1:{LISTEN_PORT} -> {PROXY[0]}:{PROXY[1]} -> {TARGET_HOST}:{TARGET_PORT}", flush=True)
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    if not TARGET_HOST:
        print("задай PG_RELAY_TARGET=<host Neon>")
        sys.exit(1)
    asyncio.run(main())
