#!/usr/bin/env python3
"""Baixa ripgrep 15.1.0 por hash fixado; extrai apenas binário e licenças."""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import tarfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = '15.1.0'
TARGETS = {
    'macos-arm64': ('aarch64-apple-darwin.tar.gz', '378e973289176ca0c6054054ee7f631a065874a352bf43f0fa60ef079b6ba715'),
    'macos-x64': ('x86_64-apple-darwin.tar.gz', '64811cb24e77cac3057d6c40b63ac9becf9082eedd54ca411b475b755d334882'),
    'linux-arm64': ('aarch64-unknown-linux-gnu.tar.gz', '2b661c6ef508e902f388e9098d9c4c5aca72c87b55922d94abdba830b4dc885e'),
    'linux-x64': ('x86_64-unknown-linux-musl.tar.gz', '1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599'),
    'windows-arm64': ('aarch64-pc-windows-msvc.zip', '00d931fb5237c9696ca49308818edb76d8eb6fc132761cb2a1bd616b2df02f8e'),
    'windows-x64': ('x86_64-pc-windows-msvc.zip', '124510b94b6baa3380d051fdf4650eaa80a302c876d611e9dba0b2e18d87493a'),
}
MAX_ARCHIVE = 32 * 1024 * 1024
MAX_MEMBER = 64 * 1024 * 1024


def instalar(dados: bytes, plataforma: str, digest: str, destino: Path, *, windows: bool) -> None:
    if hashlib.sha256(dados).hexdigest() != digest:
        raise ValueError('SHA-256 do arquivo ripgrep não confere.')
    raiz = f'ripgrep-{VERSION}-{plataforma}/'
    nomes = ['rg.exe' if windows else 'rg', 'LICENSE-MIT', 'UNLICENSE', 'COPYING']
    conteudos = {}
    if windows:
        with zipfile.ZipFile(io.BytesIO(dados)) as arq:
            for nome in nomes:
                info = arq.getinfo(raiz + nome)
                modo = info.external_attr >> 16
                if info.is_dir() or (modo and (modo & 0o170000) not in (0, 0o100000)) or info.file_size > MAX_MEMBER:
                    raise ValueError('Arquivo inválido no pacote ripgrep.')
                conteudos[nome] = arq.read(info)
    else:
        with tarfile.open(fileobj=io.BytesIO(dados), mode='r:gz') as arq:
            for nome in nomes:
                info = arq.getmember(raiz + nome)
                if not info.isfile() or info.size > MAX_MEMBER:
                    raise ValueError('Arquivo inválido no pacote ripgrep.')
                with arq.extractfile(info) as item:
                    conteudos[nome] = item.read()
    destino.mkdir(parents=True, exist_ok=True)
    for nome in nomes:
        alvo = destino / (nome if nome.startswith('rg') else 'ripgrep-' + nome)
        alvo.write_bytes(conteudos[nome])
        if nome == 'rg':
            os.chmod(alvo, 0o755)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, choices=TARGETS)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--archive', type=Path, help='Arquivo previamente baixado; o hash fixado continua obrigatório.')
    args = parser.parse_args()
    suffix, digest = TARGETS[args.target]
    nome = f'ripgrep-{VERSION}-{suffix}'
    if args.archive:
        with args.archive.open('rb') as arquivo:
            dados = arquivo.read(MAX_ARCHIVE + 1)
    else:
        url = f'https://github.com/BurntSushi/ripgrep/releases/download/{VERSION}/{nome}'
        with urllib.request.urlopen(url, timeout=60) as resposta:
            dados = resposta.read(MAX_ARCHIVE + 1)
    if len(dados) > MAX_ARCHIVE:
        raise ValueError('Arquivo ripgrep excede o limite.')
    plataforma = suffix.removesuffix('.tar.gz').removesuffix('.zip')
    instalar(dados, plataforma, digest, args.output, windows=args.target.startswith('windows-'))


if __name__ == '__main__':
    main()
