"""Alterações POSIX por descritores com comparação do conteúdo aprovado."""
from __future__ import annotations

import os
from pathlib import Path

import arquivos_seguros


def substituir(caminho, anterior, novo):
    alvo = Path(os.path.abspath(caminho))
    raiz = Path(alvo.anchor)
    if anterior is None:
        arquivos_seguros.criar(raiz, alvo, novo)
        return
    texto, versao = arquivos_seguros.ler(raiz, alvo)
    if texto != anterior:
        raise ValueError("O arquivo mudou após a aprovação.")
    if not versao.arquivo[-1] & 0o222:
        raise PermissionError("Arquivo sem permissão de escrita.")
    arquivos_seguros.editar(raiz, alvo, versao, novo)



def remover(caminho, anterior):
    alvo = Path(os.path.abspath(caminho))
    raiz = Path(alvo.anchor)
    texto, versao = arquivos_seguros.ler(raiz, alvo)
    if texto != anterior:
        raise ValueError("O arquivo mudou após a aprovação.")
    with arquivos_seguros._pai(raiz, alvo) as (pai, nome, pais):
        if pais != versao.pais:
            raise ValueError("Diretório mudou após a aprovação.")
        fd, info = arquivos_seguros._abrir_arquivo(pai, nome)
        try:
            if arquivos_seguros._estado(info) != versao.arquivo:
                raise ValueError("O arquivo mudou após a aprovação.")
            atual = os.stat(nome, dir_fd=pai, follow_symlinks=False)
            if arquivos_seguros._estado(atual) != versao.arquivo:
                raise ValueError("O arquivo mudou após a aprovação.")
            os.unlink(nome, dir_fd=pai)
        finally:
            os.close(fd)

def _main():
    import json
    import sys
    try:
        dados = json.load(sys.stdin)
        if not isinstance(dados, dict) or not isinstance(dados.get('caminho'), str):
            raise ValueError('Dados inválidos para alteração.')
        if dados.get('operacao') == 'remover':
            if not isinstance(dados.get('anterior'), str):
                raise ValueError('Conteúdo anterior inválido.')
            remover(dados['caminho'], dados['anterior'])
            return 0
        if not isinstance(dados.get('novo'), str):
            raise ValueError('Dados inválidos para alteração.')
        if dados.get('anterior') is not None and not isinstance(dados['anterior'], str):
            raise ValueError('Conteúdo anterior inválido.')
        substituir(dados['caminho'], dados.get('anterior'), dados['novo'])
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Alteração segura recusada: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
