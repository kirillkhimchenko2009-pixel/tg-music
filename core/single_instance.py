"""Защита от дублей процессов: один экземпляр бота/сервера/туннеля.

Windows: удерживаем блокировку файла на всё время жизни процесса;
на крэше ОС отпускает сама, поэтому stale-локов не бывает."""
import msvcrt
import pathlib

_LOCKS: list = []

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"


def acquire(name: str) -> None:
    """Raise SystemExit, если уже запущен экземпляр с этим именем."""
    DATA_DIR.mkdir(exist_ok=True)
    f = (DATA_DIR / f"lock_{name}.lock").open("w", encoding="ascii")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit(f"[{name}] уже запущен — второй экземпляр не стартую")
    # строка-пометка; дескриптор держим открытым до конца процесса
    try:
        f.write(str(pathlib.Path(__file__).name))
        f.flush()
    except Exception:
        pass
    _LOCKS.append(f)
