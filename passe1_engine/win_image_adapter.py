"""Adapta imagens validadas ao staging privado do Windows."""
from __future__ import annotations

from contextlib import contextmanager

from passe1_engine.win_private import staging


@contextmanager
def preparar_imagens(imagens):
    """Mantém os handles privados abertos até o consumidor terminar.

    Erros da criação e da limpeza são propagados ao chamador.
    """
    with staging(imagens) as caminhos:
        yield caminhos
