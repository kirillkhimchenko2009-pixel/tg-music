"""TCP forwarder for cloudflared edge connections through a local SOCKS5/HTTP proxy.

The ISP on this machine kills long-lived direct TCP connections to Cloudflare
edge (198.41.x.x:443) after ~40-60s (DPI). The v2rayN proxy works fine.
cloudflared cannot dial edges via a proxy natively, so we run this forwarder:

    cloudflared -> 127.0.0.1:8441..8444 -> (socks5 v2rayN) -> real edge ip:443

Run:  python tools/edge_forwarder.py
"""
import asyncio
import sys

SOCKS_HOST = "127.0.0.1"
SOCKS_PORT = 10808
HTTP_PROXY = ("127.0.0.1", 10809)  # fallback mode

# listen port -> real edge ip (port 443)
MAPPING = [
    (8441, "198.41.192.47"),
    (8442, "198.41.192.167"),
    (8443, "198.41.200.233"),
    (8444, "198.41.200.43"),
]
BIND_HOST = "127.0.0.1"


SOCKS_USER = None  # optionally set (INCY uses "incy_xxx" + password from .secrets)
SOCKS_PASS = None


async def socks5_connect(host: str, port: int):
    """Open a connection to host:port via SOCKS5 proxy (auth optional)."""
    reader, writer = await asyncio.open_connection(SOCKS_HOST, SOCKS_PORT)
    methods = b"\x05\x01\x00" if not SOCKS_USER else b"\x05\x02\x00\x02"
    writer.write(methods)
    await writer.drain()
    resp = await reader.readexactly(2)
    if resp[0] != 5:
        raise RuntimeError(f"socks5 greeting failed: {resp!r}")
    if resp[1] == 2:
        if not SOCKS_USER:
            raise RuntimeError("socks5 requires auth but no credentials configured")
        import base64
        u = SOCKS_USER.encode()
        p = SOCKS_PASS.encode()
        writer.write(b"\x01" + len(u).to_bytes(1, "big") + u + len(p).to_bytes(1, "big") + p)
        await writer.drain()
        r = await reader.readexactly(2)
        if r[1] != 0:
            raise RuntimeError("socks5 auth failed")
    # request: CONNECT, IPv4
    ip = bytes(int(p) for p in host.split("."))
    req = b"\x05\x01\x00\x01" + ip + port.to_bytes(2, "big")
    writer.write(req)
    await writer.drain()
    head = await reader.readexactly(4)
    if head[1] != 0:
        raise RuntimeError(f"socks5 connect failed: code={head[1]}")
    # read remaining bound address (variable length by atyp)
    atyp = head[3]
    if atyp == 1:       # ipv4 + port
        await reader.readexactly(6)
    elif atyp == 3:     # domain (len byte) + port
        ln = (await reader.readexactly(1))[0]
        await reader.readexactly(ln + 2)
    elif atyp == 4:     # ipv6 + port
        await reader.readexactly(18)
    return reader, writer


async def http_connect(host: str, port: int):
    """Open a connection to host:port via HTTP CONNECT proxy."""
    reader, writer = await asyncio.open_connection(*HTTP_PROXY)
    writer.write(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    await writer.drain()
    status = await reader.readline()
    if not status or " 200" not in status:
        raise RuntimeError(f"http connect failed: {status!r}")
    # drain headers
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
    return reader, writer


async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter):
    try:
        while True:
            data = await src.read(65536)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except Exception:
        pass
    finally:
        try:
            dst.close()
        except Exception:
            pass


async def handle(client_r, client_w, target):
    try:
        try:
            up_r, up_w = await http_connect(*target)
        except Exception:
            up_r, up_w = await socks5_connect(*target)
    except Exception as e:
        print(f"[forwarder] dial {target} failed: {e}", file=sys.stderr, flush=True)
        try:
            client_w.close()
        except Exception:
            pass
        return
    # success -> log once per accepted conn
    print(f"[forwarder] {target} linked via proxy", file=sys.stderr, flush=True)
    t1 = asyncio.create_task(pump(client_r, up_w))
    t2 = asyncio.create_task(pump(up_r, client_w))
    try:
        await asyncio.gather(t1, t2)
    finally:
        try:
            up_w.close()
            client_w.close()
        except Exception:
            pass


async def main():
    runners = []
    for port, ip in MAPPING:
        target = (ip, 443)

        async def cb(r, w, t=target):
            await asyncio.create_task(handle(r, w, t))

        srv = await asyncio.start_server(cb, BIND_HOST, port)
        print(f"[forwarder] {BIND_HOST}:{port} -> {ip}:443", flush=True)
        runners.append(srv)
    print("[forwarder] ready", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
