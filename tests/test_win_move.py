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


def test_escrita_no_handle_preserva_identidade_e_limita_tamanho(tmp_path):
    arquivo = tmp_path / 'arquivo'
    arquivo.write_bytes(b'antigo-com-sobra')
    identidade = arquivo.stat().st_ino
    fd = os.open(arquivo, os.O_RDWR | getattr(os, 'O_BINARY', 0))
    try:
        win_move._gravar_handle(fd, b'novo')
    finally:
        os.close(fd)
    assert arquivo.stat().st_ino == identidade
    assert arquivo.read_bytes() == b'novo'


def test_falha_de_escrita_deixa_estado_reconhecivel(tmp_path, monkeypatch):
    arquivo = tmp_path / 'arquivo'
    antigo, novo = b'conteudo antigo', b'CONTEUDO novo'
    arquivo.write_bytes(antigo)
    fd = os.open(arquivo, os.O_RDWR | getattr(os, 'O_BINARY', 0))
    escrever = os.write
    chamadas = []

    def falhar(fd_atual, dados):
        chamadas.append(1)
        if len(chamadas) > 1:
            raise OSError('falha simulada')
        return escrever(fd_atual, dados[:4])

    monkeypatch.setattr(win_move.os, 'write', falhar)
    try:
        with pytest.raises(OSError, match='falha simulada'):
            win_move._gravar_handle(fd, novo)
    finally:
        os.close(fd)
    assert win_move._escrita_interrompida(antigo, novo, arquivo.read_bytes())


def test_restaurar_exige_confirmacao_e_recusa_conteudo_divergente(tmp_path):
    arquivo = tmp_path / 'arquivo'
    antigo, novo = b'conteudo antigo', b'CONTEUDO novo'
    arquivo.write_bytes(b'CONT' + antigo[4:])
    fd = os.open(arquivo, os.O_RDWR | getattr(os, 'O_BINARY', 0))
    try:
        with pytest.raises(ValueError, match='confirmação'):
            win_move._restaurar_conteudo(fd, antigo, novo)
        win_move._restaurar_conteudo(fd, antigo, novo, confirmado=True)
        assert arquivo.read_bytes() == antigo
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, b'terceiro')
        with pytest.raises(ValueError, match='divergiu'):
            win_move._restaurar_conteudo(fd, antigo, novo, confirmado=True)
        assert arquivo.read_bytes().startswith(b'terceiro')
    finally:
        os.close(fd)


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


def test_recusa_envelope_maior_que_limite_de_recuperacao(tmp_path, monkeypatch):
    monkeypatch.setattr(win_move, 'LIMITE_EDICAO', 1100)
    plano = {'origem': str(tmp_path / 'origem'), 'id_origem': ['1', '2', '3'], 'conteudo': '\x00' * 100}
    with pytest.raises(ValueError, match='limite de recuperação'):
        win_move._serializar_plano(plano)


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
    with win_move._par_fixo(origem, destino, plano) as (descritores, diretorios):
        assert set(descritores) == {'origem', 'destino'}
        assert set(diretorios) == {'origem', 'destino'}
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


@pytest.mark.skipif(os.name != 'nt', reason='requer handles do Windows')
def test_temporario_destino_ausente_e_removido_ao_fechar(tmp_path):
    from passe1_engine import win_secure

    destino = tmp_path / 'destino-novo.txt'
    pais = win_secure.snapshot_pais(destino)['pais']
    with win_move._preparar_arquivo_temporario(destino, pais) as (fd, _pai, nome, identidade):
        assert identidade and nome.startswith('passe1-stage-')
        win_move._gravar_handle(fd, b'conteudo')
        assert not destino.exists()
    assert not (tmp_path / nome).exists()
    assert not destino.exists()


@pytest.mark.skipif(os.name != 'nt', reason='requer handles do Windows')
def test_temporario_destino_ausente_some_apos_queda_do_processo(tmp_path):
    import subprocess
    import sys

    destino = tmp_path / 'destino-novo.txt'
    programa = '''
import os, sys
from passe1_engine import win_move, win_secure
destino = sys.argv[1]
pais = win_secure.snapshot_pais(destino)['pais']
with win_move._preparar_arquivo_temporario(destino, pais) as (fd, _, _, _):
    win_move._gravar_handle(fd, b'conteudo')
    os._exit(91)
'''
    resultado = subprocess.run([sys.executable, '-c', programa, str(destino)], capture_output=True, timeout=60, check=False)
    assert resultado.returncode == 91, resultado.stderr.decode('utf-8', errors='replace')[-1000:]
    assert not list(tmp_path.glob('passe1-stage-*'))
    assert not destino.exists()


@pytest.mark.skipif(os.name != 'nt', reason='requer handles do Windows')
def test_temporario_pode_ser_retido_depois_do_journal(tmp_path):
    from passe1_engine import win_secure

    destino = tmp_path / 'destino-novo.txt'
    pais = win_secure.snapshot_pais(destino)['pais']
    with win_move._preparar_arquivo_temporario(destino, pais) as (fd, _pai, nome, _identidade):
        win_move._gravar_handle(fd, b'conteudo')
        win_move._reter_temporario(fd)
    assert (tmp_path / nome).read_bytes() == b'conteudo'


@pytest.mark.skipif(os.name != 'nt', reason='requer handles do Windows')
def test_primitivas_combina_journal_escrita_e_quarentena(tmp_path):
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
    with win_move._par_fixo(origem, destino, plano) as (descritores, diretorios), win_move._journal_fixo(tmp_path, plano) as (journal, _identidade):
        win_move._gravar_handle(descritores['destino'], b'novo')
        win_move._renomear_handle(descritores['origem'], diretorios['origem'], 'quarentena.txt')
    assert not origem.exists()
    assert destino.read_text(encoding='utf-8') == 'novo'
    assert (tmp_path / 'quarentena.txt').read_text(encoding='utf-8') == 'origem'
    assert journal.exists() and b'origem' not in journal.read_bytes()


@pytest.mark.skipif(os.name != 'nt', reason='requer handles do Windows')
def test_movimentacao_experimental_preserva_original_e_journal(tmp_path):
    import json

    from passe1_engine import win_secure

    origem, destino = tmp_path / 'origem.txt', tmp_path / 'destino.txt'
    origem.write_text('origem', encoding='utf-8')
    destino.write_text('destino', encoding='utf-8')
    vo, vd = win_secure.snapshot(origem)['identidade'], win_secure.snapshot(destino)['identidade']
    journal = win_move._mover_experimental(origem, destino, 'origem', 'destino', 'novo', vo, vd, tmp_path)
    plano = json.loads(win_secure._ler_journal(journal)['conteudo'])
    assert not origem.exists()
    assert destino.read_text(encoding='utf-8') == 'novo'
    assert (tmp_path / plano['quarentena']).read_text(encoding='utf-8') == 'origem'
    assert b'destino' not in journal.read_bytes()
    revisao = win_move._revisar_experimental(journal)
    assert revisao['origem_em_quarentena'] is True
    assert revisao['origem'] == str(origem) and revisao['destino'] == str(destino)
    assert revisao['conteudo_origem'] == 'origem'
    assert revisao['conteudo_destino_anterior'] == 'destino'
    assert revisao['conteudo_destino_atual'] == b'novo'
    with pytest.raises(ValueError, match='confirmação'):
        win_move._recuperar_experimental(journal, revisao['revisao'])
    win_move._recuperar_experimental(journal, revisao['revisao'], confirmado=True)
    assert origem.read_text(encoding='utf-8') == 'origem'
    assert destino.read_text(encoding='utf-8') == 'destino'
    assert not (tmp_path / plano['quarentena']).exists()
    assert journal.exists()
    with pytest.raises(ValueError, match='já utilizada'):
        win_move._recuperar_experimental(journal, revisao['revisao'], confirmado=True)
    with pytest.raises(ValueError, match='inválida'):
        win_move._recuperar_experimental(journal, 'token-forjado', confirmado=True)


@pytest.mark.skipif(os.name != 'nt', reason='requer handles do Windows')
def test_movimentacao_interrompida_mantem_original_e_journal(tmp_path, monkeypatch):
    import json

    from passe1_engine import win_secure

    origem, destino = tmp_path / 'origem.txt', tmp_path / 'destino.txt'
    origem.write_text('origem', encoding='utf-8')
    destino.write_text('destino', encoding='utf-8')
    vo, vd = win_secure.snapshot(origem)['identidade'], win_secure.snapshot(destino)['identidade']

    def interromper(*_args):
        raise OSError('falha simulada')

    monkeypatch.setattr(win_move, '_renomear_handle', interromper)
    with pytest.raises(RuntimeError, match='journal preservado'):
        win_move._mover_experimental(origem, destino, 'origem', 'destino', 'novo', vo, vd, tmp_path)
    journal = next(tmp_path.glob('move-*.journal'))
    plano = json.loads(win_secure._ler_journal(journal)['conteudo'])
    assert plano['conteudo_destino'] == 'destino'
    assert origem.read_text(encoding='utf-8') == 'origem'
    assert destino.read_text(encoding='utf-8') == 'novo'
    revisao = win_move._revisar_experimental(journal)
    assert revisao['origem_em_quarentena'] is False
    win_move._recuperar_experimental(journal, revisao['revisao'], confirmado=True)
    assert destino.read_text(encoding='utf-8') == 'destino'


@pytest.mark.skipif(os.name != 'nt', reason='requer handles do Windows')
def test_recuperacao_experimental_recusa_alteracao_apos_revisao(tmp_path):
    from passe1_engine import win_secure

    origem, destino = tmp_path / 'origem com espaços.txt', tmp_path / 'destino.txt'
    origem.write_text('origem', encoding='utf-8')
    destino.write_text('destino', encoding='utf-8')
    vo, vd = win_secure.snapshot(origem)['identidade'], win_secure.snapshot(destino)['identidade']
    journal = win_move._mover_experimental(origem, destino, 'origem', 'destino', 'novo', vo, vd, tmp_path)
    revisao = win_move._revisar_experimental(journal)
    destino.write_text('terceiro', encoding='utf-8')
    with pytest.raises(ValueError):
        win_move._recuperar_experimental(journal, revisao['revisao'], confirmado=True)
    assert destino.read_text(encoding='utf-8') == 'terceiro'
    with pytest.raises(ValueError, match='já utilizada'):
        win_move._recuperar_experimental(journal, revisao['revisao'], confirmado=True)


@pytest.mark.skipif(os.name != 'nt', reason='requer handles do Windows')
@pytest.mark.parametrize('fase', ['escrita', 'quarentena'])
def test_recuperacao_experimental_apos_processo_interrompido(tmp_path, fase):
    import subprocess
    import sys

    origem, destino = tmp_path / 'origem.txt', tmp_path / 'destino.txt'
    origem.write_text('origem', encoding='utf-8')
    destino.write_text('destino', encoding='utf-8')
    programa = '''
import os, sys
from passe1_engine import win_move, win_secure
origem, destino, raiz, fase = sys.argv[1:]
vo = win_secure.snapshot(origem)['identidade']
vd = win_secure.snapshot(destino)['identidade']
def interromper(fd, dados):
    os.write(fd, dados[:1])
    os.fsync(fd)
    os._exit(91)
if fase == 'escrita':
    win_move._gravar_handle = interromper
else:
    renomear = win_move._renomear_handle
    def interromper_renomeacao(*args):
        renomear(*args)
        os._exit(91)
    win_move._renomear_handle = interromper_renomeacao
win_move._mover_experimental(origem, destino, 'origem', 'destino', 'novo', vo, vd, raiz)
'''
    resultado = subprocess.run([sys.executable, '-c', programa, str(origem), str(destino), str(tmp_path), fase], capture_output=True, timeout=60, check=False)
    assert resultado.returncode == 91, resultado.stderr.decode('utf-8', errors='replace')[-1000:]
    journal = next(tmp_path.glob('move-*.journal'))
    if fase == 'escrita':
        assert origem.read_text(encoding='utf-8') == 'origem'
        assert destino.read_text(encoding='utf-8') == 'nestino'
    else:
        assert not origem.exists()
        assert destino.read_text(encoding='utf-8') == 'novo'
    revisao = win_move._revisar_experimental(journal)
    win_move._recuperar_experimental(journal, revisao['revisao'], confirmado=True)
    assert origem.read_text(encoding='utf-8') == 'origem'
    assert destino.read_text(encoding='utf-8') == 'destino'


@pytest.mark.skipif(os.name != 'nt', reason='requer handles do Windows')
def test_recuperacao_interrompida_pode_ser_revisada_novamente(tmp_path):
    import subprocess
    import sys

    from passe1_engine import win_secure

    origem, destino = tmp_path / 'origem.txt', tmp_path / 'destino.txt'
    origem.write_text('origem', encoding='utf-8')
    destino.write_text('destino', encoding='utf-8')
    vo, vd = win_secure.snapshot(origem)['identidade'], win_secure.snapshot(destino)['identidade']
    journal = win_move._mover_experimental(origem, destino, 'origem', 'destino', 'novo', vo, vd, tmp_path)
    programa = '''
import os, sys
from passe1_engine import win_move
journal = sys.argv[1]
revisao = win_move._revisar_experimental(journal)
def interromper(fd, dados):
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, dados[:1])
    os.fsync(fd)
    os._exit(91)
win_move._gravar_handle = interromper
win_move._recuperar_experimental(journal, revisao['revisao'], confirmado=True)
'''
    resultado = subprocess.run([sys.executable, '-c', programa, str(journal)], capture_output=True, timeout=60, check=False)
    assert resultado.returncode == 91, resultado.stderr.decode('utf-8', errors='replace')[-1000:]
    assert destino.read_text(encoding='utf-8') == 'dovo'
    assert not origem.exists()
    nova_revisao = win_move._revisar_experimental(journal)
    win_move._recuperar_experimental(journal, nova_revisao['revisao'], confirmado=True)
    assert origem.read_text(encoding='utf-8') == 'origem'
    assert destino.read_text(encoding='utf-8') == 'destino'


@pytest.mark.skipif(os.name != 'nt', reason='requer NTFS e handles do Windows')
def test_movimentacao_e_recuperacao_preservam_acl_e_streams(tmp_path):
    import ctypes
    from ctypes import wintypes

    from passe1_engine import win_secure

    origem, destino = tmp_path / 'origem.txt', tmp_path / 'destino.txt'
    origem.write_text('origem', encoding='utf-8')
    destino.write_text('destino', encoding='utf-8')
    for arquivo, dados in ((origem, b'origem-ads'), (destino, b'destino-ads')):
        with open(str(arquivo) + ':meta', 'wb') as stream:
            stream.write(dados)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    advapi.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]

    def descritor(arquivo):
        necessario = wintypes.DWORD()
        advapi.GetFileSecurityW(str(arquivo), 7, None, 0, ctypes.byref(necessario))
        assert necessario.value > 0
        buffer = ctypes.create_string_buffer(necessario.value)
        assert advapi.GetFileSecurityW(str(arquivo), 7, buffer, len(buffer), ctypes.byref(necessario))
        return buffer.raw[:necessario.value]

    ids = (origem.stat().st_ino, destino.stat().st_ino)
    seguranca = (descritor(origem), descritor(destino))
    vo, vd = win_secure.snapshot(origem)['identidade'], win_secure.snapshot(destino)['identidade']
    journal = win_move._mover_experimental(origem, destino, 'origem', 'destino', 'novo', vo, vd, tmp_path)
    revisao = win_move._revisar_experimental(journal)
    win_move._recuperar_experimental(journal, revisao['revisao'], confirmado=True)
    assert (origem.stat().st_ino, destino.stat().st_ino) == ids
    assert (descritor(origem), descritor(destino)) == seguranca
    for arquivo, esperado in ((origem, b'origem-ads'), (destino, b'destino-ads')):
        with open(str(arquivo) + ':meta', 'rb') as stream:
            assert stream.read() == esperado
