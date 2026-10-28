# -*- coding: utf-8 -*-
"""Разовый перенос базы: локальный SQLite → Postgres (Neon).

Запуск после получения DATABASE_URL от Neon:
    DATABASE_URL="postgresql://...neon.tech/neondb?sslmode=require" \
        python tools/migrate_sqlite_to_pg.py

Берёт согласованный снапшот data/music.db (безопасно при живом WAL сервера),
создаёт таблицы в Postgres и переносит все строки, сохраняя id и продвигая
счётчики (sequences). Можно запускать повторно — таблицы сначала очищаются.
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy as sa  # noqa: E402

from core.db import engine as pg_engine  # noqa: E402
from core.models import Base  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "data" / "music.db"

TABLES = ("users", "tracks", "playlists", "playlist_tracks",
          "source_auths", "auth_codes", "jobs")

BOOL_COLS = {("users", "is_admin"), ("auth_codes", "used")}
DT_COLS = {("users", "created_at"), ("tracks", "created_at"),
           ("playlists", "created_at"), ("playlist_tracks", "added_at"),
           ("source_auths", "updated_at"), ("auth_codes", "expires_at"),
           ("auth_codes", "created_at"), ("jobs", "created_at")}

AUTOINC = ("tracks", "playlists", "playlist_tracks",
           "source_auths", "auth_codes", "jobs")  # users.id — telegram_id, без sequence


def _convert(table: str, col: str, val):
    if val is None:
        return None
    if (table, col) in BOOL_COLS:
        return bool(val)
    if (table, col) in DT_COLS and isinstance(val, str):
        return datetime.fromisoformat(val)
    return val


def read_snapshot() -> dict[str, list[dict]]:
    """Снапшот SQLite, согласованный даже при живом WAL."""
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "snapshot.db"
        src = sqlite3.connect(SRC)
        dst = sqlite3.connect(snap)
        src.backup(dst)
        src.close()
        dst.close()
        lite = sa.create_engine(f"sqlite:///{snap}")
        out: dict[str, list[dict]] = {}
        with lite.connect() as c:
            for t in TABLES:
                try:
                    res = c.execute(sa.text(f"SELECT * FROM {t}"))
                except sa.exc.OperationalError:
                    print(f"[skip] таблицы {t} нет в SQLite")
                    continue
                cols = list(res.keys())
                out[t] = [{c_: _convert(t, c_, v) for c_, v in zip(cols, row)}
                          for row in res.fetchall()]
        lite.dispose()
    return out


async def main() -> int:
    pg_url = os.getenv("DATABASE_URL", "").strip()
    if not pg_url:
        print("нет DATABASE_URL — задай переменную окружения")
        return 1
    if pg_engine.dialect.name != "postgresql":
        print(f"движок не Postgres ({pg_engine.dialect.name}) — DATABASE_URL не применился?")
        return 1

    rows = read_snapshot()
    # SQLite не включал FK — чистим висячие ссылки, иначе Postgres отвергнет вставку
    uids = {r["id"] for r in rows.get("users", [])}
    tids = {r["id"] for r in rows.get("tracks", [])}
    pids = {r["id"] for r in rows.get("playlists", [])}
    orphans = 0
    for t in ("source_auths", "auth_codes", "jobs"):
        before = len(rows.get(t) or [])
        rows[t] = [r for r in rows.get(t, []) if r["user_id"] in uids]
        orphans += before - len(rows[t])
    if "jobs" in rows:
        before = len(rows["jobs"])
        rows["jobs"] = [r for r in rows["jobs"]
                        if r.get("track_id") is None or r["track_id"] in tids]
        orphans += before - len(rows["jobs"])
    before = len(rows.get("playlist_tracks") or [])
    rows["playlist_tracks"] = [r for r in rows.get("playlist_tracks", [])
                               if r["playlist_id"] in pids and r["track_id"] in tids]
    orphans += before - len(rows["playlist_tracks"])
    if orphans:
        print(f"[warn] отброшено висячих ссылок: {orphans}")
    async with pg_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # чистим в обратном порядке зависимостей (FK), чтобы повторный запуск был безопасен
        for t in reversed(TABLES):
            if t in rows:
                await conn.execute(sa.text(f'DELETE FROM "{t}"'))
        # переносим строки в прямом порядке (users → tracks → playlists → ...)
        for t in TABLES:
            rs = rows.get(t) or []
            if not rs:
                print(f"[ok] {t}: пусто")
                continue
            cols = list(rs[0].keys())
            tbl = sa.table(t, *[sa.column(c) for c in cols])
            chunk = 500
            for i in range(0, len(rs), chunk):
                part = rs[i:i + chunk]
                await conn.execute(tbl.insert(), [{c: r[c] for c in cols} for r in part])
            print(f"[ok] {t}: {len(rs)} строк")
        # продвигаем счётчики autoincrement
        for t in AUTOINC:
            if t in rows:
                await conn.execute(sa.text(
                    f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {t}), 1))"))
    await pg_engine.dispose()
    print("готово: база перенесена в Postgres")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
