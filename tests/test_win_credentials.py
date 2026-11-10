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
