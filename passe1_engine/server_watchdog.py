"""Executa comando em grupo e encerra descendentes ao fim ou perda do servidor."""
import ctypes
import os
import select
import signal
import subprocess
import sys
import time


def main():
    canal = int(sys.argv[1])
    if select.select([canal], [], [], 0)[0] and not os.read(canal, 1):
        return 1
    pendentes = []

    def registrar(numero, _quadro):
        pendentes.append(numero)

    for numero in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(numero, registrar)
    if pendentes:
        return 128 + pendentes[0]
    processo = subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL, close_fds=True, start_new_session=True)

    def encerrar():
        try:
            os.killpg(processo.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.2)
        try:
            os.killpg(processo.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def terminou():
        if hasattr(os, "waitid"):
            return os.waitid(os.P_PID, processo.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        # Python 3.10 no macOS não expõe waitid; WNOWAIT impede reutilização do PID.
        libc = ctypes.CDLL(None, use_errno=True)
        espera = libc.waitid
        espera.argtypes = (ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int)
        espera.restype = ctypes.c_int
        info = ctypes.create_string_buffer(128)
        if espera(1, processo.pid, info, os.WEXITED | os.WNOHANG | os.WNOWAIT) != 0:
            raise OSError(ctypes.get_errno(), "waitid falhou")
        return any(info.raw)

    while True:
        if pendentes:
            encerrar()
            return 128 + pendentes[0]
        if select.select([canal], [], [], 0.1)[0] and not os.read(canal, 1):
            encerrar()
            return 1
        if terminou():
            encerrar()
            codigo = processo.wait()
            return codigo if codigo >= 0 else 128 - codigo


if __name__ == "__main__":
    raise SystemExit(main())
