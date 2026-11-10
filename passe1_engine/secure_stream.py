"""Leitura incremental de arquivos POSIX com diretórios ancorados em descritores."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import arquivos_seguros


def transmitir(caminho, destino, tamanho_bloco=65536):
    """Transmite um arquivo regular sem seguir links e sem reabrir por nome."""
    if tamanho_bloco <= 0:
        raise ValueError("Tamanho do bloco inválido.")
    alvo = Path(os.path.abspath(caminho))
    if os.name == "nt":
        fd = _abrir_windows(alvo)
        try:
            _transmitir_fd(fd, destino, tamanho_bloco)
        finally:
            os.close(fd)
        return
    raiz = Path(alvo.anchor)
    with arquivos_seguros._pai(raiz, alvo) as (pai, nome, _):
        fd, _ = arquivos_seguros._abrir_arquivo(pai, nome)
        try:
            _transmitir_fd(fd, destino, tamanho_bloco)
        finally:
            os.close(fd)


def _transmitir_fd(fd, destino, tamanho_bloco):
    antes = os.fstat(fd)
    arquivos_seguros._validar_arquivo(antes)
    while True:
        bloco = os.read(fd, tamanho_bloco)
        if not bloco:
            break
        destino.write(bloco)
    depois = os.fstat(fd)
    arquivos_seguros._validar_arquivo(depois)
    if arquivos_seguros._estado(antes) != arquivos_seguros._estado(depois):
        raise ValueError("Arquivo mudou durante a leitura.")


def _abrir_windows(alvo):
    """Valida o caminho físico do handle antes de transmitir qualquer conteúdo."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    fd = os.open(alvo, os.O_RDONLY | os.O_BINARY)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Arquivo inválido ou possui múltiplos links.")
        funcao = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
        funcao.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
        funcao.restype = wintypes.DWORD
        tamanho = funcao(msvcrt.get_osfhandle(fd), None, 0, 0)
        if not tamanho:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(tamanho + 1)
        escrito = funcao(msvcrt.get_osfhandle(fd), buffer, len(buffer), 0)
        if not escrito or escrito >= len(buffer):
            raise OSError("Não foi possível verificar o caminho do arquivo aberto.")
        final = buffer.value
        if final.startswith("\\\\?\\UNC\\"):
            final = "\\\\" + final[8:]
        elif final.startswith("\\\\?\\"):
            final = final[4:]
        if os.path.normcase(os.path.normpath(final)) != os.path.normcase(os.path.normpath(str(alvo))):
            raise ValueError("O caminho aberto contém link ou redirecionamento.")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _main():
    import sys
    if len(sys.argv) != 2:
        print("Caminho de leitura ausente.", file=sys.stderr)
        return 2
    if os.name == "nt":
        import msvcrt
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    try:
        transmitir(sys.argv[1], sys.stdout.buffer)
        sys.stdout.buffer.flush()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Leitura segura recusada: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
