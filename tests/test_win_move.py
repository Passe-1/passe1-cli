"""Verificação dos estados do protocolo experimental Windows."""
import os

import pytest

from passe1_engine import win_move


def test_classificacao_exige_identidade_e_conteudo():
    plano = {
        'id_origem': ['volume', 'origem'], 'id_destino': ['volume', 'destino'],
        'hash_origem': win_move._digest('origem'), 'hash_destino': win_move._digest('destino'),
        'hash_novo': win_move._digest('novo'),
    }
    origem = (plano['id_origem'], plano['hash_origem'])
    destino = (plano['id_destino'], plano['hash_destino'])
    novo = (plano['id_destino'], plano['hash_novo'])
    assert win_move._estado(origem, destino, plano) == 'original'
    assert win_move._estado(origem, novo, plano) == 'destino_atualizado'
    assert win_move._estado(None, novo, plano) == 'aplicado'
    assert win_move._estado((['volume', 'trocado'], plano['hash_origem']), destino, plano) == 'divergente'
    assert win_move._estado(None, destino, plano) == 'divergente'
    assert win_move._estado(origem, (plano['id_destino'], win_move._digest('mudou')), plano) == 'divergente'


def test_limite_de_conteudo_e_explicito(monkeypatch):
    monkeypatch.setattr(win_move, 'LIMITE_EDICAO', 3)
    assert win_move._digest('abc')
    with pytest.raises(ValueError, match='64 MiB'):
        win_move._digest('áá')
    with pytest.raises(TypeError, match='inválido'):
        win_move._digest(None)


def test_journal_recusa_troca_de_conteudo_identidade_ou_pais(tmp_path):
    plano = {
        'versao': 1, 'origem': str(tmp_path / 'a'), 'destino': str(tmp_path / 'b'),
        'id_origem': ['1', '2', '3'], 'id_destino': ['1', '2', '4'],
        'pais_origem': [['1', '0', '2']], 'pais_destino': [['1', '0', '2']],
    }
    for nome, texto in [('origem', 'origem'), ('destino', 'destino'), ('novo', 'novo')]:
        plano['conteudo_' + nome] = texto
        plano['hash_' + nome] = win_move._digest(texto)
    assert win_move._validar_journal(plano) == plano
    for mudanca in (
        {'conteudo_origem': 'alterado'},
        {'id_destino': plano['id_origem']},
        {'pais_origem': []},
        {'origem': 'relativo'},
    ):
        with pytest.raises(ValueError):
            win_move._validar_journal({**plano, **mudanca})


@pytest.mark.skipif(os.name != 'nt', reason='requer DPAPI do Windows')
def test_journal_dpapi_protege_e_autentica_conteudos(tmp_path):
    plano = {
        'versao': 1,
        'origem': str(tmp_path / 'origem.txt'),
        'destino': str(tmp_path / 'destino.txt'),
        'id_origem': ['1', '2', '3'],
        'id_destino': ['1', '2', '4'],
        'pais_origem': [['1', '0', '2']],
        'pais_destino': [['1', '0', '2']],
    }
    for nome, texto in [('origem', 'segredo da origem'), ('destino', 'segredo do destino'), ('novo', 'texto novo')]:
        plano['conteudo_' + nome] = texto
        plano['hash_' + nome] = win_move._digest(texto)
    cifrado = win_move._proteger_journal(plano)
    assert b'segredo' not in cifrado
    assert win_move._abrir_journal(cifrado) == plano
    alterado = bytearray(cifrado)
    alterado[len(alterado) // 2] ^= 1
    with pytest.raises((OSError, ValueError)):
        win_move._abrir_journal(bytes(alterado))
