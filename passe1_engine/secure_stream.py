"""Leitura incremental de arquivos POSIX com diretórios ancorados em descritores."""
from __future__ import annotations

import os
from pathlib import Path

import arquivos_seguros


def transmitir(caminho, destino, tamanho_bloco=65536):
    """Transmite um arquivo regular sem seguir links e sem reabrir por nome."""
    if tamanho_bloco <= 0:
        raise ValueError("Tamanho do bloco inválido.")
    alvo = Path(os.path.abspath(caminho))
    if os.name == "nt":
        from passe1_engine import win_secure
        with win_secure.abrir_leitura(alvo) as (fd, identidade):
            _transmitir_fd(fd, destino, tamanho_bloco)
            return identidade
    raiz = Path(alvo.anchor)
    with arquivos_seguros._pai(raiz, alvo) as (pai, nome, pais):
        fd, _ = arquivos_seguros._abrir_arquivo(pai, nome)
        try:
            arquivo = _transmitir_fd(fd, destino, tamanho_bloco)
            return {'arquivo': [str(item) for item in arquivo], 'pais': [[str(valor) for valor in item] for item in pais]}
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
    return arquivos_seguros._estado(depois)


def _main():
    import json
    import sys
    if len(sys.argv) != 2:
        print("Caminho de leitura ausente.", file=sys.stderr)
        return 2
    if os.name == "nt":
        import msvcrt
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    try:
        versao = transmitir(sys.argv[1], sys.stdout.buffer)
        sys.stdout.buffer.flush()
        print(json.dumps(versao), file=sys.stderr, flush=True)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Leitura segura recusada: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
