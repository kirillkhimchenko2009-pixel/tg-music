"""Асинхронный движок SQLAlchemy + фабрика сессий.

База выбирается так:
- DATABASE_URL из окружения → Postgres (Neon/Supabase; облако-деплой);
- иначе локальный SQLite (data/music.db), как раньше.
"""
import os
import ssl as ssl_mod

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (AsyncEngine, AsyncSession, async_sessionmaker,
                                    create_async_engine)

from .config import settings


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return f"sqlite+aiosqlite:///{settings.DB_PATH}"
    # Neon/Supabase отдают postgres:// — приводим к диалекту asyncpg
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix):]
            break
    return url


def create_engine() -> AsyncEngine:
    url = _database_url()
    if url.startswith("postgresql+asyncpg"):
        from urllib.parse import parse_qsl, urlencode, urlunparse, urlparse
        p = urlparse(url)
        q = dict(parse_qsl(p.query))
        server_settings: dict | None = None
        if "options" in q:
            # Neon через релей без SNI (tools/pg_relay.py): endpoint передаётся
            # в startup-пакете как options=endpoint%3D<endpoint-id>
            server_settings = {"options": q.pop("options")}
        sslmode = q.pop("sslmode", None)
        q.pop("channel_binding", None)
        url = urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), p.fragment))
        kwargs: dict = {"echo": False, "pool_pre_ping": True}
        if sslmode or server_settings:
            ctx = ssl_mod.create_default_context()
            if (p.hostname or "") in ("127.0.0.1", "localhost", "::1"):
                # релей туннелирует до настоящего Neon: имя 127.0.0.1 не совпадает
                # с сертом, но цепочку CA всё равно проверяем
                ctx.check_hostname = False
            connect_args: dict = {"ssl": ctx}
            if server_settings:
                connect_args["server_settings"] = server_settings
            kwargs["connect_args"] = connect_args
        return create_async_engine(url, **kwargs)
    return create_async_engine(url, echo=False)


engine: AsyncEngine = create_engine()

if engine.dialect.name == "sqlite":
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragma(dbapi_conn, _rec):
        """WAL + busy_timeout: почти не возникает 'database is locked'
        при одновременной работе бота и сервера с одной базой."""
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_session() -> AsyncSession:
    """Сессия для FastAPI Depends."""
    async with AsyncSessionLocal() as session:
        yield session

async def init_db() -> None:
    from .models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
