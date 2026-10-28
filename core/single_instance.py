"""Защита от дублей процессов: один экземпляр бота/сервера/туннеля.

Windows: msvcrt.locking; Linux (облако): fcntl.flock.
Блокировка файла держится всё время жизни процесса; при крэше ОС
отпускается сама, поэтому stale-локов не бывает."""
import pathlib
import sys

_LOCKS: list = []

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

if sys.platform == "win32":
    import msvcrt

    def _lock_file(f) -> None:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
else:
    import fcntl

    def _lock_file(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def acquire(name: str) -> None:
    """Raise SystemExit, если уже запущен экземпляр с этим именем."""
    DATA_DIR.mkdir(exist_ok=True)
    f = (DATA_DIR / f"lock_{name}.lock").open("w", encoding="ascii")
    try:
        _lock_file(f)
    except OSError:
        raise SystemExit(f"[{name}] уже запущен — второй экземпляр не стартую")
    # строка-пометка; дескриптор держим открытым до конца процесса
    try:
        f.write(str(pathlib.Path(__file__).name))
        f.flush()
    except Exception:
        pass
    _LOCKS.append(f)
