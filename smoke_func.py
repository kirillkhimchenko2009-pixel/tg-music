import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8')

async def main():
    # DB init
    from core.db import init_db
    await init_db()
    print('DB init OK')

    # link parser
    from services.links import parse
    cases = [
        'https://music.yandex.ru/users/ya.user/playlists/1234',
        'https://open.spotify.com/playlist/37i9dQZF1DXcBWIGg6659N?si=x',
        'https://spotify.com/playlist/3pLu9Y2p7p8493fP27',
        'https://zvuk.com/playlist/ZDqlDKbg',
        'https://youtube.com/playlist?list=PL_xY-123',
        'не ссылка вообще',
    ]
    for c in cases:
        print('link:', c, '->', parse(c))

    # matching
    from services.matching import similarity
    from services.providers.base import TrackMeta
    a = TrackMeta('spotify', '1', 'Это любовь', 'Скриптонит', duration=187)
    b = TrackMeta('ytmusic', '2', 'Это любовь', 'Скриптонит', duration=189)
    print('similarity same:', round(similarity(a, b), 3))

    # local catalog
    from core.db import AsyncSessionLocal
    from services import catalog
    async with AsyncSessionLocal() as s:
        t = await catalog.upsert_track(s, a)
        await s.commit()
        t2 = await catalog.get_track(s, t.id)
        print('catalog upsert/get:', t2.id, t2.title, t2.artist)

    # youtube search (network)
    try:
        from services.providers.youtube import YouTubeProvider
        res = await YouTubeProvider().search('Скриптонит Это любовь', limit=3)
        print('yt search:', len(res), [m.display() for m in res[:2]])
        if res:
            print('yt cover ok:', (res[0].cover_url or '')[:60])
    except Exception as e:
        print('yt search FAIL:', type(e).__name__, str(e)[:200])

asyncio.run(main())
print('SMOKE DONE')
