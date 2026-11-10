"""Contrato do adaptador e propagação de falhas do staging."""
import os
from contextlib import contextmanager

import pytest

from passe1_engine import win_image_adapter


def test_adaptador_mantem_contexto_aberto_e_repassa_imagens(monkeypatch):
    eventos = []
    imagens = [('.png', b'imagem')]

    @contextmanager
    def staging(recebido):
        eventos.append(('abrir', recebido))
        try:
            yield ['caminho']
        finally:
            eventos.append(('limpar', recebido))

    monkeypatch.setattr(win_image_adapter, 'staging', staging)
    with win_image_adapter.preparar_imagens(imagens) as caminhos:
        assert caminhos == ['caminho']
        assert eventos == [('abrir', imagens)]
    assert eventos == [('abrir', imagens), ('limpar', imagens)]


def test_adaptador_propaga_falha_de_limpeza(monkeypatch):
    @contextmanager
    def staging(_imagens):
        yield ['caminho']
        raise OSError('falha de limpeza')

    monkeypatch.setattr(win_image_adapter, 'staging', staging)
    with pytest.raises(OSError, match='falha de limpeza'), win_image_adapter.preparar_imagens([('.png', b'imagem')]):
        pass


def test_adaptador_limpa_quando_consumidor_falha(monkeypatch):
    eventos = []

    @contextmanager
    def staging(_imagens):
        try:
            yield ['caminho']
        finally:
            eventos.append('limpou')

    monkeypatch.setattr(win_image_adapter, 'staging', staging)
    with pytest.raises(RuntimeError, match='consumidor'), win_image_adapter.preparar_imagens([('.png', b'imagem')]):
        raise RuntimeError('consumidor')
    assert eventos == ['limpou']


@pytest.mark.skipif(os.name != 'nt', reason='requer Windows')
def test_adaptador_usa_staging_privado_real():
    with win_image_adapter.preparar_imagens([('.png', b'imagem')]) as caminhos:
        caminho = caminhos[0]
        assert caminho.read_bytes() == b'imagem'
        assert caminho.exists()
    assert not caminho.exists()
