"""Роутеры бота."""
from .start import router as start_router
from .kblabels import router as kblabels_router
from .account import router as account_router
from .search import router as search_router
from .links import router as links_router
from .playlists import router as playlists_router
from .bulk import router as bulk_router
from .appshare import router as appshare_router
from .wave import router as wave_router
from .musicbot import router as musicbot_router
from bot.batch import router as batch_router

ALL = [kblabels_router, start_router, playlists_router, batch_router, account_router,
       bulk_router, appshare_router, wave_router, musicbot_router, links_router,
       search_router]
