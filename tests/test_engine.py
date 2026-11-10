import json
from pathlib import Path
from unittest.mock import patch

from passe1_engine import launcher


def test_interface_configurada_sem_expor_chave(tmp_path, monkeypatch, capsys):
    capturado = {}
    def executar(binario, argumentos, ambiente):
        capturado.update(binario=binario, argumentos=argumentos, ambiente=ambiente)
        raise SystemExit(0)
    monkeypatch.setenv("OPENCODE_CONFIG", "/config/global")
    with patch.object(launcher.Path, 'is_file', return_value=True), \
         patch.object(launcher.passe1, '_ler_chave', return_value='sk-passe1-ficticia'), \
         patch.object(launcher.passe1, '_dir_config', return_value=tmp_path), \
         patch.object(launcher, '_catalogo', return_value={'glm-5.3-flash': {'name': 'glm-5.3-flash', 'tool_call': True}}), \
         patch.object(launcher.os, 'execvpe', side_effect=executar):
        try:
            launcher.iniciar([])
        except SystemExit as exc:
            assert exc.code == 0
    env = capturado['ambiente']
    config = json.loads(env['OPENCODE_CONFIG_CONTENT'])
    assert config['enabled_providers'] == ['passe1']
    assert config['permission']['edit'] == config['permission']['bash'] == 'ask'
    assert config['permission']['webfetch'] == config['permission']['websearch'] == 'ask'
    assert config['permission']['passe1_sensitive_read'] == 'ask'
    assert config['permission']['read']['*.env*'] == 'ask'
    assert config['permission']['read']['*id_rsa*'] == 'ask'
    assert config['permission']['read']['*service-account*'] == 'ask'
    assert 'PASSE1_API_KEY' not in env
    auth = tmp_path / 'opencode' / 'data' / 'opencode' / 'auth.json'
    assert json.loads(auth.read_text())['passe1']['key'] == 'sk-passe1-ficticia'
    if launcher.os.name == 'posix':
        assert auth.stat().st_mode & 0o777 == 0o600
    assert 'OPENCODE_CONFIG' not in env
    assert 'sk-passe1-ficticia' not in capsys.readouterr().out
    assert '--pure' in capturado['argumentos']


def test_sem_chave_pede_login_antes_de_iniciar(tmp_path):
    with patch.object(launcher.Path, 'is_file', return_value=True), \
         patch.object(launcher.passe1, '_ler_chave', side_effect=[None, 'sk-passe1-ficticia']), \
         patch.object(launcher.passe1, '_dir_config', return_value=tmp_path), \
         patch.object(launcher, '_catalogo', return_value={'glm-5.3-flash': {'name': 'glm-5.3-flash', 'tool_call': True}}), \
         patch.object(launcher.passe1, 'main', return_value=0) as main, \
         patch.object(launcher.os, 'execvpe', side_effect=SystemExit(0)):
        try:
            launcher.iniciar([])
        except SystemExit as exc:
            assert exc.code == 0
        main.assert_called_once_with(['login'])


def test_comando_sem_argumentos_abre_interface_em_terminal(monkeypatch):
    import passe1
    monkeypatch.setattr(passe1.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(passe1.sys.stdout, 'isatty', lambda: True)
    with patch.object(launcher, 'iniciar', return_value=0) as iniciar:
        assert passe1.main([]) == 0
        iniciar.assert_called_once_with([])


def test_catalogo_mostra_modelos_sem_inventar_capacidades(monkeypatch):
    import httpx
    dados = {'data': [{'id': 'glm-5.3-flash', 'tipo': 'chat'}, {'id': 'imagem-modelo', 'vision': True}, {'id': 'jev', 'tipo': 'decisao'}, {'id': 'ruim\x1b'}]}
    monkeypatch.setattr(launcher.passe1, '_cliente', lambda base, timeout: httpx.Client(base_url=base, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=dados))))
    modelos = launcher._catalogo('https://teste/v1', 'sk-passe1-ficticia', 'glm-5.3-flash')
    assert set(modelos) == {'glm-5.3-flash', 'imagem-modelo'}
    assert modelos['glm-5.3-flash']['tool_call'] is True
    assert modelos['imagem-modelo']['attachment'] is True
    assert 'tool_call' not in modelos['imagem-modelo']


def test_preserva_instrucoes_do_projeto(tmp_path, monkeypatch):
    (tmp_path / '.git').mkdir()
    instrucoes = tmp_path / 'AGENTS.md'
    instrucoes.write_text('Instruções do projeto')
    sub = tmp_path / 'sub'
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert Path(launcher._instrucoes()[0]).read_text() == instrucoes.read_text()


def test_logout_remove_credencial_do_motor(tmp_path, monkeypatch):
    import passe1
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path))
    launcher._guardar_credencial('sk-passe1-ficticia')
    passe1._guardar_chave('sk-passe1-ficticia')
    assert passe1.main(['logout']) == 0
    assert not (tmp_path / 'opencode' / 'data' / 'opencode' / 'auth.json').exists()


def test_preserva_claude_md_quando_nao_ha_agents(tmp_path, monkeypatch):
    (tmp_path / '.git').mkdir()
    instrucoes = tmp_path / 'CLAUDE.md'
    instrucoes.write_text('Instruções')
    monkeypatch.chdir(tmp_path)
    assert Path(launcher._instrucoes()[0]).read_text() == instrucoes.read_text()


def test_logout_apaga_chave_mesmo_se_motor_falhar(tmp_path, monkeypatch):
    import passe1
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path))
    passe1._guardar_chave('sk-passe1-ficticia')
    with patch.object(launcher, 'limpar_credencial', side_effect=OSError('falha')):
        assert passe1.main(['logout']) == 1
    assert not (tmp_path / 'key').exists()


def test_agent_interativo_sem_opcoes_abre_a_mesma_tui(monkeypatch):
    import passe1
    monkeypatch.setattr(passe1, '_ler_chave', lambda *args: 'sk-passe1-ficticia')
    monkeypatch.setattr(passe1.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(passe1.sys.stdout, 'isatty', lambda: True)
    with patch.object(launcher, 'iniciar', return_value=0) as iniciar:
        assert passe1.main(['agent']) == 0
        iniciar.assert_called_once_with([])


def test_agente_legado_com_imagem_e_sessao_cria_titulo_no_motor(tmp_path, monkeypatch):
    import argparse
    import subprocess
    imagem = tmp_path / 'imagem.png'
    imagem.write_bytes(b'\x89PNG\r\n\x1a\nconteudo')
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(base_url=None, model='glm-5.3-flash', session='projeto', sandbox=False, image=[str(imagem)], tarefa=['analise'])
    capturado = {}
    with patch.object(launcher.Path, 'is_file', return_value=True), \
         patch.object(launcher.passe1, '_ler_chave', return_value='sk-passe1-ficticia'), \
         patch.object(launcher.passe1, '_dir_config', return_value=tmp_path), \
         patch.object(launcher, '_catalogo', return_value={'glm-5.3-flash': {'name': 'modelo'}}), \
         patch.object(launcher.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '[]', '')), \
         patch.object(launcher.os, 'execvpe', side_effect=lambda b, a, e: capturado.update(args=a)):
        launcher.iniciar([], agente=args)
    assert '--title' in capturado['args']
    assert capturado['args'][capturado['args'].index('--file') + 1] == str(imagem)
    assert capturado['args'].index('analise') < capturado['args'].index('--file')


def test_agente_legado_recusa_sandbox_nao_suportado(tmp_path, capsys):
    import argparse
    args = argparse.Namespace(base_url=None, model=None, session=None, sandbox=True, image=None, tarefa=['teste'])
    with patch.object(launcher.Path, 'is_file', return_value=True), \
         patch.object(launcher.passe1, '_ler_chave', return_value='sk-passe1-ficticia'), \
         patch.object(launcher.passe1, '_dir_config', return_value=tmp_path), \
         patch.object(launcher, '_catalogo', return_value={}), \
         patch.object(launcher.os, 'execvpe') as executar:
        assert launcher.iniciar([], agente=args) == 1
        executar.assert_not_called()
    assert 'macOS' in capsys.readouterr().out


def test_instrucoes_recusa_hardlink_para_segredo(tmp_path, monkeypatch):
    import os
    (tmp_path / '.git').mkdir()
    segredo = tmp_path / '.env'
    segredo.write_text('segredo')
    os.link(segredo, tmp_path / 'AGENTS.md')
    monkeypatch.chdir(tmp_path)
    assert launcher._instrucoes() == []
