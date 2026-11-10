"""Proteção de credenciais em diretório com herança compartilhada."""
import os
import subprocess

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='requer Windows')


def test_credenciais_novas_sao_privadas_em_pasta_compartilhada(tmp_path):
    from passe1_engine import win_credentials

    compartilhada = tmp_path / 'compartilhada'
    compartilhada.mkdir()
    subprocess.run(['icacls', str(compartilhada), '/grant', '*S-1-1-0:(OI)(CI)F'], capture_output=True, check=True, timeout=30)
    for nome in ('key', 'session', 'key-session', 'auth.json'):
        arquivo = compartilhada / nome
        win_credentials.gravar(arquivo, b'conteudo-ficticio')
        assert win_credentials.ler(arquivo) == b'conteudo-ficticio'
        acl = subprocess.run(['icacls', str(arquivo)], capture_output=True, text=True, errors='replace', check=True, timeout=30).stdout
        assert '(I)' not in acl
        assert 'Everyone' not in acl and 'S-1-1-0' not in acl
        win_credentials.gravar(arquivo, b'novo')
        assert win_credentials.ler(arquivo) == b'novo'


def test_credencial_antiga_herdada_e_recusada_sem_ser_lida_ou_alterada(tmp_path):
    from passe1_engine import win_credentials

    compartilhada = tmp_path / 'compartilhada'
    compartilhada.mkdir()
    subprocess.run(['icacls', str(compartilhada), '/grant', '*S-1-1-0:(OI)(CI)F'], capture_output=True, check=True, timeout=30)
    arquivo = compartilhada / 'key'
    arquivo.write_bytes(b'antiga')
    with pytest.raises(ValueError, match='sem proteção'):
        win_credentials.ler(arquivo)
    with pytest.raises(ValueError, match='sem proteção'):
        win_credentials.gravar(arquivo, b'nova')
    assert arquivo.read_bytes() == b'antiga'


def test_credencial_com_hardlink_e_recusada(tmp_path):
    from passe1_engine import win_credentials

    arquivo = tmp_path / 'key'
    win_credentials.gravar(arquivo, b'original')
    os.link(arquivo, tmp_path / 'alias')
    with pytest.raises(ValueError, match='links'):
        win_credentials.ler(arquivo)
    with pytest.raises(ValueError, match='links'):
        win_credentials.gravar(arquivo, b'nova')
    assert arquivo.read_bytes() == b'original'


def test_raiz_reparse_nao_recebe_credenciais(tmp_path):
    from passe1_engine import win_credentials

    real = tmp_path / 'real'
    real.mkdir()
    link = tmp_path / 'link'
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f'Criação de symlink indisponível: {exc}')
    with pytest.raises((ValueError, OSError)):
        win_credentials.gravar(link / 'session', b'segredo')
    assert not list(real.iterdir())


def test_link_final_nao_le_nem_altera_alvo(tmp_path):
    from passe1_engine import win_credentials

    alvo, link = tmp_path / 'alvo', tmp_path / 'key'
    win_credentials.gravar(alvo, b'original')
    try:
        link.symlink_to(alvo)
    except OSError as exc:
        pytest.skip(f'Criação de symlink indisponível: {exc}')
    with pytest.raises((ValueError, OSError)):
        win_credentials.ler(link)
    with pytest.raises((ValueError, OSError)):
        win_credentials.gravar(link, b'modificado')
    assert win_credentials.ler(alvo) == b'original'


def test_falha_de_gravacao_remove_credencial_nova_incompleta(tmp_path, monkeypatch):
    import ctypes

    from passe1_engine import win_credentials

    carregar = ctypes.WinDLL

    class Falha:
        argtypes = None

        def __call__(self, *_args):
            return 0

    class Kernel:
        def __init__(self, original):
            self.original = original
            self.WriteFile = Falha()

        def __getattr__(self, nome):
            return getattr(self.original, nome)

    def carregar_interceptado(nome, *args, **kwargs):
        biblioteca = carregar(nome, *args, **kwargs)
        return Kernel(biblioteca) if nome == 'kernel32' else biblioteca

    monkeypatch.setattr(ctypes, 'WinDLL', carregar_interceptado)
    arquivo = tmp_path / 'session'
    with pytest.raises(OSError, match='gravar a credencial'):
        win_credentials.gravar(arquivo, b'credencial-ficticia')
    assert not arquivo.exists()
    journal = arquivo.with_name(arquivo.name + '.passe1-cred-journal')
    assert journal.exists()
    with pytest.raises(ValueError, match='confirmação'):
        win_credentials.recuperar(arquivo)
    win_credentials.recuperar(arquivo, confirmado=True)
    assert not journal.exists()


def test_falha_de_overwrite_restaura_credencial_anterior(tmp_path, monkeypatch):
    from passe1_engine import win_credentials

    arquivo = tmp_path / 'session'
    win_credentials.gravar(arquivo, b'credencial-anterior')
    escrever = win_credentials._gravar_handle
    chamadas = []

    def falhar_uma_vez(kernel, handle, dados):
        chamadas.append(1)
        if len(chamadas) == 1:
            escrever(kernel, handle, b'parcial')
            raise OSError('falha simulada')
        return escrever(kernel, handle, dados)

    monkeypatch.setattr(win_credentials, '_gravar_handle', falhar_uma_vez)
    with pytest.raises(OSError, match='estado anterior restaurado'):
        win_credentials.gravar(arquivo, b'credencial-nova')
    assert win_credentials.ler(arquivo) == b'credencial-anterior'
    assert not arquivo.with_name(arquivo.name + '.passe1-cred-journal').exists()


@pytest.mark.parametrize('existente', [False, True])
def test_queda_em_gravacao_exige_recovery_e_preserva_estado_anterior(tmp_path, existente):
    import sys

    from passe1_engine import win_credentials

    arquivo = tmp_path / 'key'
    if existente:
        win_credentials.gravar(arquivo, b'credencial-anterior')
    programa = '''
import os, sys
from passe1_engine import win_credentials
original = win_credentials._gravar_handle
def interromper(kernel, handle, dados):
    original(kernel, handle, b'parcial')
    os._exit(91)
win_credentials._gravar_handle = interromper
win_credentials.gravar(sys.argv[1], b'credencial-nova')
'''
    resultado = subprocess.run([sys.executable, '-c', programa, str(arquivo)], capture_output=True, timeout=60, check=False)
    assert resultado.returncode == 91, resultado.stderr.decode('utf-8', errors='replace')[-1000:]
    with pytest.raises(win_credentials.RecoveryPendente):
        win_credentials.ler(arquivo)
    with pytest.raises(ValueError, match='confirmação'):
        win_credentials.recuperar(arquivo)
    win_credentials.recuperar(arquivo, confirmado=True)
    if existente:
        assert win_credentials.ler(arquivo) == b'credencial-anterior'
    else:
        assert not arquivo.exists()
    assert not arquivo.with_name(arquivo.name + '.passe1-cred-journal').exists()


def test_fluxo_real_de_login_grava_e_le_arquivos_privados(tmp_path, monkeypatch):
    import passe1
    from passe1_engine import launcher, win_credentials

    config = tmp_path / 'compartilhada'
    config.mkdir()
    subprocess.run(['icacls', str(config), '/grant', '*S-1-1-0:(OI)(CI)F'], capture_output=True, check=True, timeout=30)
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(config))
    passe1._guardar_sessao('https://passe1.exemplo', 'passe1_session', 'token-ficticio')
    passe1._guardar_chave('sk-passe1-ficticia')
    assert passe1._ler_sessao()['token'] == 'token-ficticio'
    assert passe1._ler_chave('https://passe1.exemplo') == 'sk-passe1-ficticia'
    launcher._guardar_credencial('sk-passe1-ficticia')
    arquivos = [config / 'key', config / 'session', config / 'key-session', config / 'opencode/data/opencode/auth.json']
    for arquivo in arquivos:
        assert win_credentials.ler(arquivo)
        acl = subprocess.run(['icacls', str(arquivo)], capture_output=True, text=True, errors='replace', check=True, timeout=30).stdout
        assert '(I)' not in acl and 'Everyone' not in acl and 'S-1-1-0' not in acl


@pytest.mark.parametrize('nome', ['key', 'session', 'key-session', 'auth.json'])
def test_credencial_legada_insegura_e_recusada_com_orientacao(tmp_path, monkeypatch, nome):
    import passe1
    from passe1_engine import launcher

    config = tmp_path / 'compartilhada'
    config.mkdir()
    subprocess.run(['icacls', str(config), '/grant', '*S-1-1-0:(OI)(CI)F'], capture_output=True, check=True, timeout=30)
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(config))
    if nome == 'key-session':
        passe1._guardar_sessao('https://passe1.exemplo', 'passe1_session', 'token-ficticio')
    arquivo = config / ('opencode/data/opencode/auth.json' if nome == 'auth.json' else nome)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_bytes(b'credencial-antiga')
    with pytest.raises(ValueError, match='remova o arquivo e faça login novamente'):
        if nome == 'session':
            passe1._ler_sessao()
        elif nome in ('key', 'key-session'):
            passe1._ler_chave('https://passe1.exemplo')
        else:
            launcher._guardar_credencial('sk-passe1-nova')
    assert arquivo.read_bytes() == b'credencial-antiga'
