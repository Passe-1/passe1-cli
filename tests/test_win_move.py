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


@pytest.mark.parametrize(('anterior', 'novo', 'atual', 'esperado'), [
    (b'abcdef', b'XYZ', b'abcdef', True),
    (b'abcdef', b'XYZ', b'XYcdef', True),
    (b'abcdef', b'XYZ', b'XYZdef', True),
    (b'abcdef', b'XYZ', b'XYZ', True),
    (b'abc', b'UVWXYZ', b'UVc', True),
    (b'abc', b'UVWXYZ', b'UVWX', True),
    (b'abcdef', b'XYZ', b'XbcZef', False),
    (b'abcdef', b'XYZ', b'XY', False),
    (b'abc', b'UVWXYZ', b'UVxq', False),
])
def test_reconhece_somente_escrita_sequencial_possivelmente_interrompida(anterior, novo, atual, esperado):
    assert win_move._escrita_interrompida(anterior, novo, atual) is esperado


def test_escrita_interrompida_equivale_a_todos_prefixos_pequenos():
    from itertools import product

    valores = [bytes(palavra) for tamanho in range(4) for palavra in product(b'ab', repeat=tamanho)]
    for anterior in valores:
        for novo in valores:
            possiveis = {novo} | {novo[:indice] + anterior[indice:] for indice in range(len(novo) + 1)}
            for atual in valores:
                assert win_move._escrita_interrompida(anterior, novo, atual) == (atual in possiveis)


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


@pytest.mark.skipif(os.name != 'nt', reason='requer DPAPI do Windows')
def test_journal_fixo_persiste_cifrado_e_impede_renomeacao(tmp_path):
    plano = {
        'versao': 1, 'origem': str(tmp_path / 'a'), 'destino': str(tmp_path / 'b'),
        'id_origem': ['1', '2', '3'], 'id_destino': ['1', '2', '4'],
        'pais_origem': [['1', '0', '2']], 'pais_destino': [['1', '0', '2']],
    }
    for nome, texto in [('origem', 'segredo-origem'), ('destino', 'segredo-destino'), ('novo', 'novo')]:
        plano['conteudo_' + nome] = texto
        plano['hash_' + nome] = win_move._digest(texto)
    with win_move._journal_fixo(tmp_path, plano) as (arquivo, identidade):
        assert identidade
        with pytest.raises(OSError):
            os.rename(arquivo, arquivo.with_name('trocado'))
    assert arquivo.exists()
    assert b'segredo' not in arquivo.read_bytes()


@pytest.mark.skipif(os.name != 'nt', reason='requer handles do Windows')
def test_par_fixo_confere_identidades_e_impede_trocas(tmp_path):
    from passe1_engine import win_secure

    origem, destino = tmp_path / 'origem.txt', tmp_path / 'destino.txt'
    origem.write_text('origem', encoding='utf-8')
    destino.write_text('destino', encoding='utf-8')
    vo, vd = win_secure.snapshot(origem)['identidade'], win_secure.snapshot(destino)['identidade']
    plano = {
        'versao': 1, 'origem': str(origem), 'destino': str(destino),
        'id_origem': vo['arquivo'], 'id_destino': vd['arquivo'],
        'pais_origem': vo['pais'], 'pais_destino': vd['pais'],
    }
    for nome, texto in [('origem', 'origem'), ('destino', 'destino'), ('novo', 'novo')]:
        plano['conteudo_' + nome] = texto
        plano['hash_' + nome] = win_move._digest(texto)
    with win_move._par_fixo(origem, destino, plano) as descritores:
        assert set(descritores) == {'origem', 'destino'}
        with pytest.raises(OSError):
            os.rename(origem, origem.with_name('movido'))
        with pytest.raises(OSError):
            destino.write_text('ataque', encoding='utf-8')
    destino.write_text('alterado', encoding='utf-8')
    with pytest.raises(ValueError, match='Conteúdo mudou'), win_move._par_fixo(origem, destino, plano):
        pytest.fail('Não deve aceitar arquivo alterado.')
    assert origem.read_text(encoding='utf-8') == 'origem'


@pytest.mark.skipif(os.name != 'nt', reason='requer handles do Windows')
def test_par_fixo_recusa_troca_de_ancestral_apos_snapshot(tmp_path, monkeypatch):
    from passe1_engine import win_secure

    pasta, movido = tmp_path / 'projeto', tmp_path / 'movido'
    pasta.mkdir()
    origem, destino = pasta / 'origem.txt', pasta / 'destino.txt'
    origem.write_text('origem', encoding='utf-8')
    destino.write_text('destino', encoding='utf-8')
    vo, vd = win_secure.snapshot(origem)['identidade'], win_secure.snapshot(destino)['identidade']
    plano = {
        'versao': 1, 'origem': str(origem), 'destino': str(destino),
        'id_origem': vo['arquivo'], 'id_destino': vd['arquivo'],
        'pais_origem': vo['pais'], 'pais_destino': vd['pais'],
    }
    for nome, texto in [('origem', 'origem'), ('destino', 'destino'), ('novo', 'novo')]:
        plano['conteudo_' + nome] = texto
        plano['hash_' + nome] = win_move._digest(texto)
    snapshot_original = win_move.snapshot_pais
    tentativa = []

    def trocar(caminho):
        resultado = snapshot_original(caminho)
        if not tentativa:
            try:
                pasta.rename(movido)
            except OSError:
                tentativa.append('bloqueado')
            else:
                tentativa.append('trocado')
                pasta.mkdir()
                (pasta / 'origem.txt').write_text('origem', encoding='utf-8')
                (pasta / 'destino.txt').write_text('destino', encoding='utf-8')
        return resultado

    monkeypatch.setattr(win_move, 'snapshot_pais', trocar)
    if_erro = False
    try:
        with win_move._par_fixo(origem, destino, plano):
            assert tentativa == ['bloqueado']
    except (OSError, ValueError):
        if_erro = True
    assert tentativa and (tentativa == ['bloqueado'] or if_erro)
    original = movido if tentativa == ['trocado'] else pasta
    assert (original / 'origem.txt').read_text(encoding='utf-8') == 'origem'
    assert (original / 'destino.txt').read_text(encoding='utf-8') == 'destino'


@pytest.mark.skipif(os.name != 'nt', reason='requer handles do Windows')
def test_renomeacao_por_handle_nao_sobrescreve_destino(tmp_path):
    import ctypes
    import msvcrt
    from ctypes import wintypes

    origem, existente = tmp_path / 'origem.txt', tmp_path / 'existente.txt'
    origem.write_text('origem', encoding='utf-8')
    existente.write_text('não sobrescrever', encoding='utf-8')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    pai = kernel.CreateFileW(str(tmp_path), 0x100084, 3, None, 3, 0x02200000, None)
    assert pai != wintypes.HANDLE(-1).value
    handle = kernel.CreateFileW(str(origem), 0xC0010080, 0, None, 3, 0x00200000, None)
    assert handle != wintypes.HANDLE(-1).value
    fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    try:
        with pytest.raises(OSError):
            win_move._renomear_handle(fd, pai, existente.name)
        win_move._renomear_handle(fd, pai, 'quarentena.txt')
    finally:
        os.close(fd)
        kernel.CloseHandle(pai)
    assert not origem.exists()
    assert (tmp_path / 'quarentena.txt').read_text(encoding='utf-8') == 'origem'
    assert existente.read_text(encoding='utf-8') == 'não sobrescrever'
