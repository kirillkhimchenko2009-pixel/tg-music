"""Функциональный смоук: конфиг, БД, парсер ссылок, матчинг, поиск, сервер."""
import asyncio
import sys
sys.stdout.reconfigure(encoding='utf-8')

async def main():
    from core.db import init_db
    await init_db()
    print('DB init OK', flush=True)

    from services.links import parse
    cases = [
        'https://music.yandex.ru/users/125243012/playlists/2077',
        'https://open.spotify.com/playlist/37i9dQZF1DXcBWIGg6659N?si=x',
        'https://spotify.com/playlist/3pLu9Y2p7p8493fP27',
        'https://zvuk.com/playlist/ZDqlDKbg',
        'https://youtube.com/playlist?list=PL_xY-123',
        'https://www.youtube.com/watch?v=abcdefghijk',
    ]
    for c in cases:
        print('link:', c, '->', parse(c), flush=True)

    from services.matching import similarity
    from services.providers.base import TrackMeta
    a = TrackMeta('spotify', '1', 'Это любовь', 'Скриптонит', duration=187)
    b = TrackMeta('ytmusic', '2', 'Это любовь', 'Скриптонит', duration=189)
    print('similarity:', round(similarity(a, b), 3), flush=True)

    # сервер через TestClient
    from fastapi.testclient import TestClient
    from server.main import app
    with TestClient(app) as client:
        r = client.get('/api/ping')
        print('ping:', r.status_code, r.json(), flush=True)
        r = client.get('/api/library', headers={'X-User-Id': '777'})
        print('library:', r.status_code, len(r.json().get('tracks', [])), flush=True)
        r = client.get('/api/search?q=', headers={'X-User-Id': '777'})
        print('search empty:', r.status_code, r.json(), flush=True)
        r = client.get('/')
        print('index:', r.status_code, ('app' in r.text), flush=True)

    # yt поиск с fallback
    try:
        from services.providers.youtube import YouTubeProvider
        res = await YouTubeProvider().search('scryptonite itten', limit=3)
        print('yt search:', len(res), flush=True)
    except Exception as e:
        print('yt search FAIL:', type(e).__name__, str(e)[:150], flush=True)

asyncio.run(main())
print('SMOKE2 DONE')
