import importlib, sys

mods = [
    'core.config', 'core.db', 'core.models', 'services.links', 'services.matching',
    'services.catalog', 'services.downloader', 'services.tags', 'services.queue',
    'services.ingest', 'services.net', 'services.pipeline', 'services.authstore',
    'services.providers.youtube', 'services.providers.yandex',
    'services.providers.zvuk', 'services.providers.spotify',
    'bot.main', 'bot.runtime', 'bot.keyboards', 'bot.state', 'bot.batch',
    'bot.handlers.start', 'bot.handlers.search', 'bot.handlers.links',
    'bot.handlers.playlists', 'bot.handlers.bulk', 'bot.handlers.account',
    'server.main', 'server.auth',
]
ok = True
for m in mods:
    try:
        importlib.import_module(m)
        print('OK  ', m, flush=True)
    except Exception as e:
        ok = False
        print('FAIL', m, '->', type(e).__name__, str(e)[:300], flush=True)
print('ALL OK' if ok else 'ERRORS PRESENT', flush=True)
