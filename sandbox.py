"""Executa comandos aprovados sem escrita e rede no macOS.

O perfil não limita a leitura de arquivos acessíveis ao usuário.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import processos


def executar(comando: str, raiz: Path, timeout: float = 120, limite: int = 30000):
    """Retorna (código, saída, expirou); falha fechada sem sandbox compatível."""
    ferramenta = Path("/usr/bin/sandbox-exec")
    if sys.platform != "darwin" or not os.access(ferramenta, os.X_OK):
        raise RuntimeError("Sandbox de comandos indisponível nesta plataforma.")
    projeto = Path(raiz).resolve(strict=True)
    if not projeto.is_dir() or projeto == Path(projeto.anchor):
        raise ValueError("Diretório do projeto inválido para o sandbox.")
    perfil = "(version 1)(allow default)(deny network*)(deny file-write*)"
    return processos.executar(
        [str(ferramenta), "-p", perfil, "/bin/sh", "-c", comando],
        projeto, timeout, limite,
    )
