"""Trava cooperativa entre builds e alterações do submódulo OpenCode."""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path


@contextlib.contextmanager
def segurar(raiz, timeout=60):
    raiz = Path(raiz).resolve()
    gitdir = subprocess.check_output(['git', 'rev-parse', '--git-common-dir'], cwd=raiz, text=True).strip()
    pasta = Path(gitdir)
    if not pasta.is_absolute():
        pasta = raiz / pasta
    arquivo = pasta / 'passe1-vendor.lock'
    fd = os.open(arquivo, os.O_RDWR | os.O_CREAT | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    bloqueado = False
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b'0')
        limite = time.monotonic() + timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                bloqueado = True
                break
            except (OSError, BlockingIOError):
                if time.monotonic() >= limite:
                    raise TimeoutError('Outro processo está usando o vendor; tente novamente.') from None
                time.sleep(0.1)
        yield
    finally:
        if bloqueado:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def main():
    if len(sys.argv) < 3 or sys.argv[1] != '--':
        raise SystemExit('Uso: python scripts/vendor_lock.py -- comando [argumentos...]')
    raiz = Path(__file__).resolve().parents[1]
    with segurar(raiz):
        return subprocess.call(sys.argv[2:], cwd=raiz)


if __name__ == '__main__':
    raise SystemExit(main())
