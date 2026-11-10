import argparse
import contextlib
import io
import os
from unittest.mock import patch
from pathlib import Path

import pytest

import passe1
from passe1_engine import secure_move, secure_stream

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='transação POSIX')


def test_recovery_exige_terminal_e_nao_revela_previa_ao_pipe(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(passe1.sys.stdin, 'isatty', lambda: False)
    with patch.object(secure_move, 'revisar_local') as revisar:
        assert passe1.cmd_recover_move(argparse.Namespace(journal=str(tmp_path))) == 1
        revisar.assert_not_called()
    assert 'conteudo-confidencial' not in capsys.readouterr().out


def test_recovery_mostra_diff_sanitizado_e_exige_confirmacao(tmp_path, monkeypatch, capsys):
    origem, destino, raiz = tmp_path / 'origem', tmp_path / 'destino', tmp_path / 'journal'
    raiz.mkdir(mode=0o700)
    origem.write_text('original\x1b[31m\n')
    destino.write_text('destino antigo\n')
    io_origem, io_destino = io.BytesIO(), io.BytesIO()
    id_origem = secure_stream.transmitir(origem, io_origem)
    id_destino = secure_stream.transmitir(destino, io_destino)
    journal = secure_move.mover(origem, destino, io_origem.getvalue().decode(), io_destino.getvalue().decode(), 'novo\n', id_origem, id_destino, raiz)
    args = argparse.Namespace(journal=str(journal))
    monkeypatch.setattr(passe1.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(passe1.sys.stdout, 'isatty', lambda: True)
    with patch('builtins.input', return_value='n'):
        assert passe1.cmd_recover_move(args) == 1
    saida = capsys.readouterr().out
    assert 'original' in saida and '\x1b' not in saida
    assert not origem.exists() and destino.read_text() == 'novo\n'
    with patch('builtins.input', return_value='sim'):
        assert passe1.cmd_recover_move(args) == 0
    assert origem.read_text() == 'original\x1b[31m\n'
    assert destino.read_text() == 'destino antigo\n'


def test_recovery_real_em_pty_exibe_diff_e_respeita_recusa(tmp_path):
    import pexpect
    import sys
    origem, destino, raiz = tmp_path / 'origem', tmp_path / 'destino', tmp_path / 'journal'
    raiz.mkdir(mode=0o700)
    origem.write_text('origem aprovada\n')
    destino.write_text('destino aprovado\n')
    a, b = io.BytesIO(), io.BytesIO()
    ida, idb = secure_stream.transmitir(origem, a), secure_stream.transmitir(destino, b)
    journal = secure_move.mover(origem, destino, a.getvalue().decode(), b.getvalue().decode(), 'novo\n', ida, idb, raiz)
    instalado = os.environ.get('PASSE1_TEST_INSTALLED') == '1'
    argumentos = (['-I'] if instalado else []) + ['-m', 'passe1', 'recover-move', str(journal)]
    cwd = tmp_path if instalado else Path(passe1.__file__).parent
    processo = pexpect.spawn(sys.executable, argumentos, cwd=str(cwd), encoding='utf-8', timeout=20)
    try:
        processo.expect('origem aprovada')
        processo.expect('Restaurar o estado revisado')
        processo.sendline('n')
        processo.expect('Recuperação cancelada')
        processo.expect(pexpect.EOF)
    finally:
        processo.close(force=True)
    assert not origem.exists() and destino.read_text() == 'novo\n'


def test_preview_windows_exige_confirmacao_e_sanitiza(tmp_path, monkeypatch, capsys):
    from passe1_engine import win_move
    revisao = {
        'origem': str(tmp_path / 'origem'),
        'destino': str(tmp_path / 'destino'),
        'conteudo_origem': 'origem\x1b[31m',
        'conteudo_destino_anterior': 'antes\n',
        'conteudo_destino_atual': b'depois\n',
        'revisao': 'token-ficticio',
    }
    monkeypatch.setattr(passe1.sys, 'platform', 'win32')
    monkeypatch.setattr(passe1.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(passe1.sys.stdout, 'isatty', lambda: True)
    monkeypatch.setattr(win_move, '_abrir_plano_fixo', lambda _: contextlib.nullcontext((None, None, {})))
    args = argparse.Namespace(journal=str(tmp_path / 'journal'))
    with patch.object(win_move, '_revisar_experimental', return_value=revisao), \
         patch.object(win_move, '_recuperar_experimental') as recuperar, \
         patch('builtins.input', return_value='n'):
        assert passe1.cmd_recover_move(args) == 1
        recuperar.assert_not_called()
    saida = capsys.readouterr().out
    assert 'origem' in saida and 'antes' in saida and '\x1b' not in saida
    with patch.object(win_move, '_revisar_experimental', return_value=revisao), \
         patch.object(win_move, '_recuperar_experimental') as recuperar, \
         patch('builtins.input', return_value='sim'):
        assert passe1.cmd_recover_move(args) == 0
        recuperar.assert_called_once_with(Path(args.journal), 'token-ficticio', confirmado=True)


def test_preview_windows_mostra_bytes_invalidos_sem_ocultar(tmp_path, monkeypatch, capsys):
    from passe1_engine import win_move
    revisao = {'origem': 'origem', 'destino': 'destino', 'conteudo_origem': 'texto', 'conteudo_destino_anterior': 'antes', 'conteudo_destino_atual': b'parcial\xff', 'revisao': 'token'}
    monkeypatch.setattr(passe1.sys, 'platform', 'win32')
    monkeypatch.setattr(passe1.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(passe1.sys.stdout, 'isatty', lambda: True)
    monkeypatch.setattr(win_move, '_abrir_plano_fixo', lambda _: contextlib.nullcontext((None, None, {})))
    with patch.object(win_move, '_revisar_experimental', return_value=revisao), patch('builtins.input', return_value='n'):
        assert passe1.cmd_recover_move(argparse.Namespace(journal=str(tmp_path / 'journal'))) == 1
    assert r'\xff' in capsys.readouterr().out


def test_preview_windows_destino_ausente_preserva_stage(tmp_path, monkeypatch, capsys):
    from passe1_engine import win_move
    revisao = {'origem': 'origem', 'destino': 'destino', 'conteudo_origem': 'texto', 'conteudo_destino_anterior': None, 'conteudo_destino_atual': b'', 'revisao': 'token', 'requer_limpeza_manual': True, 'stage_caminho': 'stage\x1b[31m'}
    monkeypatch.setattr(passe1.sys, 'platform', 'win32')
    monkeypatch.setattr(passe1.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(passe1.sys.stdout, 'isatty', lambda: True)
    monkeypatch.setattr(win_move, '_abrir_plano_fixo', lambda _: contextlib.nullcontext((None, None, {'destino_ausente': True})))
    with patch.object(win_move, '_revisar_ausente_experimental', return_value=revisao), \
         patch.object(win_move, '_recuperar_ausente_experimental', return_value={'stage_preservado': True, 'stage_caminho': revisao['stage_caminho']}) as recuperar, \
         patch('builtins.input', return_value='sim'):
        assert passe1.cmd_recover_move(argparse.Namespace(journal=str(tmp_path / 'journal'))) == 0
        recuperar.assert_called_once()
    saida = capsys.readouterr().out
    assert 'Destino originalmente inexistente' in saida and 'Stage não apagado' in saida
    assert '\x1b' not in saida
