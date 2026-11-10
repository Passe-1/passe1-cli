import argparse
from unittest.mock import patch

import passe1


def test_recuperacao_exige_confirmacao_e_nao_exibe_conteudo(tmp_path, monkeypatch, capsys):
    from passe1_engine import win_secure
    alvo = tmp_path / 'arquivo.txt'
    args = argparse.Namespace(caminho=str(alvo))
    monkeypatch.setattr(passe1.sys, 'platform', 'win32')
    monkeypatch.setattr(passe1.sys.stdin, 'isatty', lambda: True)
    with patch.object(win_secure, 'recuperar') as recuperar, patch('builtins.input', return_value='n'):
        assert passe1.cmd_recover(args) == 1
        recuperar.assert_not_called()
    with patch.object(win_secure, 'recuperar') as recuperar, patch('builtins.input', return_value='sim'):
        assert passe1.cmd_recover(args) == 0
        recuperar.assert_called_once_with(alvo)
    assert 'segredo-ficticio' not in capsys.readouterr().out


def test_recuperacao_sem_terminal_falha_fechada(tmp_path, monkeypatch):
    monkeypatch.setattr(passe1.sys, 'platform', 'win32')
    monkeypatch.setattr(passe1.sys.stdin, 'isatty', lambda: False)
    assert passe1.cmd_recover(argparse.Namespace(caminho=str(tmp_path / 'arquivo'))) == 1
