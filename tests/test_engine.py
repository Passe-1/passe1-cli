import json
import os
import pytest
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
         patch.object(launcher, '_supervision', side_effect=lambda a, env: capturado.update(args=a, imagem=Path(a[a.index('--file') + 1]).read_bytes()) or 0):
        launcher.iniciar([], agente=args)
    assert '--title' in capturado['args']
    assert capturado['imagem'] == imagem.read_bytes()
    assert capturado['args'].index('analise') < capturado['args'].index('--file')


def test_agente_legado_recusa_sandbox_nao_suportado(tmp_path, capsys, monkeypatch):
    import argparse
    args = argparse.Namespace(base_url=None, model=None, session=None, sandbox=True, image=None, tarefa=['teste'])
    monkeypatch.setattr(launcher.sys, 'platform', 'linux')
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


def test_agent_modelo_em_terminal_usa_tui_completa(monkeypatch):
    import passe1
    monkeypatch.setattr(passe1.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(passe1.sys.stdout, 'isatty', lambda: True)
    with patch.object(launcher, 'iniciar', return_value=0) as iniciar:
        assert passe1.main(['agent', '--model', 'modelo']) == 0
        assert iniciar.call_args.kwargs['tui'] is True
        assert iniciar.call_args.kwargs['agente'].model == 'modelo'


def test_agent_sandbox_em_terminal_usa_tui_completa(monkeypatch):
    import passe1
    monkeypatch.setattr(passe1.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(passe1.sys.stdout, 'isatty', lambda: True)
    with patch.object(launcher, 'iniciar', return_value=0) as iniciar:
        assert passe1.main(['agent', '--sandbox']) == 0
        assert iniciar.call_args.kwargs['tui'] is True
        assert iniciar.call_args.kwargs['agente'].sandbox is True


def test_tui_completa_inicia_sessao_e_prepara_imagem(tmp_path, monkeypatch):
    import argparse
    import subprocess
    imagem = tmp_path / 'imagem.png'
    imagem.write_bytes(b'\x89PNG\r\n\x1a\nconteudo')
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(base_url=None, model='glm-5.3-flash', session='projeto', sandbox=False, image=[str(imagem)], tarefa=[])
    capturado = {}
    respostas = [subprocess.CompletedProcess([], 0, '[]', ''), subprocess.CompletedProcess([], 0, '{"id":"ses_teste"}', '')]
    with patch.object(launcher.Path, 'is_file', return_value=True), \
         patch.object(launcher.passe1, '_ler_chave', return_value='sk-passe1-ficticia'), \
         patch.object(launcher.passe1, '_dir_config', return_value=tmp_path), \
         patch.object(launcher, '_catalogo', return_value={'glm-5.3-flash': {'name': 'modelo'}}), \
         patch.object(launcher.subprocess, 'run', side_effect=respostas), \
         patch.object(launcher, '_supervision', side_effect=lambda a, env: capturado.update(args=a, imagem=Path(a[a.index('--image') + 1]).read_bytes()) or 0):
        launcher.iniciar([], agente=args, tui=True)
    assert 'run' not in capturado['args']
    assert capturado['args'][capturado['args'].index('--session') + 1] == 'ses_teste'
    assert capturado['imagem'] == imagem.read_bytes()
    assert not Path(capturado['args'][capturado['args'].index('--image') + 1]).exists()


def test_agent_imagem_e_sessao_em_terminal_usa_tui(monkeypatch):
    import passe1
    monkeypatch.setattr(passe1.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(passe1.sys.stdout, 'isatty', lambda: True)
    with patch.object(launcher, 'iniciar', return_value=0) as iniciar:
        assert passe1.main(['agent', '--session', 'projeto', '--image', 'foto.png']) == 0
        assert iniciar.call_args.kwargs['tui'] is True
        assert iniciar.call_args.kwargs['agente'].image == ['foto.png']


def test_tui_anexa_bytes_validados_mesmo_se_origem_mudar(tmp_path, monkeypatch):
    import argparse
    imagem = tmp_path / 'foto.png'
    validada = b'\x89PNG\r\n\x1a\noriginal'
    imagem.write_bytes(validada)
    args = argparse.Namespace(base_url=None, model='glm-5.3-flash', session=None, sandbox=False, image=[str(imagem)], tarefa=[])
    ler_original = launcher.passe1._imagens
    capturado = {}
    def trocar(caminhos):
        partes = ler_original(caminhos)
        imagem.write_bytes(b'\x89PNG\r\n\x1a\nsubstituida')
        return partes
    def executar(argumentos, env):
        caminho = Path(argumentos[argumentos.index('--image') + 1])
        capturado.update(dados=caminho.read_bytes(), diretorio=caminho.parent)
        return 0
    with patch.object(launcher.Path, 'is_file', return_value=True), \
         patch.object(launcher.passe1, '_ler_chave', return_value='sk-passe1-ficticia'), \
         patch.object(launcher.passe1, '_dir_config', return_value=tmp_path), \
         patch.object(launcher, '_catalogo', return_value={'glm-5.3-flash': {'name': 'modelo'}}), \
         patch.object(launcher.passe1, '_imagens', side_effect=trocar), \
         patch.object(launcher, '_supervision', side_effect=executar):
        launcher.iniciar([], agente=args, tui=True)
    assert capturado['dados'] == validada
    assert not capturado['diretorio'].exists()


def test_agent_nao_interativo_anexa_bytes_validados_se_origem_mudar(tmp_path, monkeypatch):
    import argparse
    imagem = tmp_path / 'foto.png'
    validada = b'\x89PNG\r\n\x1a\noriginal'
    imagem.write_bytes(validada)
    args = argparse.Namespace(base_url=None, model='glm-5.3-flash', session=None, sandbox=False, image=[str(imagem)], tarefa=['analise'])
    ler_original = launcher.passe1._imagens
    capturado = {}
    def trocar(caminhos):
        partes = ler_original(caminhos)
        imagem.write_bytes(b'\x89PNG\r\n\x1a\nsubstituida')
        return partes
    def executar(argumentos, env):
        caminho = Path(argumentos[argumentos.index('--file') + 1])
        capturado.update(dados=caminho.read_bytes(), diretorio=caminho.parent)
        return 0
    with patch.object(launcher.Path, 'is_file', return_value=True), \
         patch.object(launcher.passe1, '_ler_chave', return_value='sk-passe1-ficticia'), \
         patch.object(launcher.passe1, '_dir_config', return_value=tmp_path), \
         patch.object(launcher, '_catalogo', return_value={'glm-5.3-flash': {'name': 'modelo'}}), \
         patch.object(launcher.passe1, '_imagens', side_effect=trocar), \
         patch.object(launcher, '_supervision', side_effect=executar):
        launcher.iniciar([], agente=args)
    assert capturado['dados'] == validada
    assert not capturado['diretorio'].exists()


def test_supervisor_preserva_codigo_de_saida():
    import sys
    assert launcher._supervision([sys.executable, '-c', 'raise SystemExit(7)'], {}) == 7


def test_supervisor_encaminha_sinal_recebido_durante_spawn(monkeypatch):
    import signal
    class Processo:
        recebido = []
        def send_signal(self, sinal):
            self.recebido.append(sinal)
        def wait(self):
            return -signal.SIGTERM
        def poll(self):
            return None
    processo = Processo()
    def iniciar(*args, **kwargs):
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        return processo
    monkeypatch.setattr(launcher.subprocess, 'Popen', iniciar)
    assert launcher._supervision(['motor'], {}) == 128 + signal.SIGTERM
    assert processo.recebido == [signal.SIGTERM]


@pytest.mark.skipif(os.name == 'nt', reason='limpeza POSIX; Windows coberto pelo adaptador DACL')
def test_falha_ao_limpar_staging_nao_e_silenciada(tmp_path):
    import argparse
    imagem = tmp_path / 'foto.png'
    imagem.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    args = argparse.Namespace(base_url=None, model=None, session=None, sandbox=False, image=[str(imagem)], tarefa=['teste'])
    criar_temp = launcher.tempfile.mkdtemp
    with patch.object(launcher.tempfile, 'mkdtemp', side_effect=lambda prefix: criar_temp(prefix=prefix, dir=tmp_path)), \
         patch.object(launcher.Path, 'is_file', return_value=True), \
         patch.object(launcher.passe1, '_ler_chave', return_value='sk-passe1-ficticia'), \
         patch.object(launcher.passe1, '_dir_config', return_value=tmp_path), \
         patch.object(launcher, '_catalogo', return_value={'glm-5.3-flash': {'name': 'modelo'}}), \
         patch.object(launcher, '_supervision', return_value=0), \
         patch.object(launcher.shutil, 'rmtree', side_effect=OSError('falha')):
        import pytest
        with pytest.raises(ValueError, match='remover o staging'):
            launcher.iniciar([], agente=args)


def test_staging_windows_usa_bytes_validados_e_aguarda_supervisor(tmp_path, monkeypatch):
    import argparse
    from contextlib import contextmanager
    from passe1_engine import win_image_adapter
    imagem = tmp_path / 'foto.png'
    dados = b'\x89PNG\r\n\x1a\noriginal'
    imagem.write_bytes(dados)
    staged = tmp_path / 'staged.png'
    eventos = []
    @contextmanager
    def staging(itens):
        eventos.append(('abriu', itens))
        staged.write_bytes(itens[0][1])
        try:
            yield [staged]
        finally:
            eventos.append(('limpou',))
            staged.unlink()
    def executar(comando, ambiente):
        eventos.append(('executou', staged.read_bytes(), ambiente['PASSE1_IMAGE_HASHES']))
        assert comando[comando.index('--file') + 1] == str(staged)
        return 0
    args = argparse.Namespace(base_url=None, model=None, session=None, sandbox=False, image=[str(imagem)], tarefa=['analise'])
    monkeypatch.setattr(launcher, '_eh_windows', lambda: True)
    with patch.object(launcher.Path, 'is_file', return_value=True), \
         patch.object(launcher.passe1, '_ler_chave', return_value='sk-passe1-ficticia'), \
         patch.object(launcher.passe1, '_dir_config', return_value=tmp_path), \
         patch.object(launcher, '_catalogo', return_value={'glm-5.3-flash': {'name': 'modelo'}}), \
         patch.object(win_image_adapter, 'preparar_imagens', side_effect=staging), \
         patch.object(launcher, '_supervision', side_effect=executar):
        assert launcher.iniciar([], agente=args) == 0
    assert eventos[0] == ('abriu', [('.png', dados)])
    assert eventos[1][0:2] == ('executou', dados)
    assert eventos[2] == ('limpou',)
    assert not staged.exists()


def test_windows_distingue_erro_do_motor_e_de_limpeza(tmp_path, monkeypatch):
    import argparse
    from contextlib import contextmanager
    import pytest
    from passe1_engine import win_image_adapter
    imagem = tmp_path / 'foto.png'
    imagem.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    staged = tmp_path / 'staged.png'
    staged.write_bytes(imagem.read_bytes())
    args = argparse.Namespace(base_url=None, model=None, session=None, sandbox=False, image=[str(imagem)], tarefa=['teste'])
    monkeypatch.setattr(launcher, '_eh_windows', lambda: True)
    @contextmanager
    def preparado(_itens):
        yield [staged]
    @contextmanager
    def falha_limpeza(_itens):
        yield [staged]
        raise OSError('falha limpeza')
    with patch.object(launcher.Path, 'is_file', return_value=True), \
         patch.object(launcher.passe1, '_ler_chave', return_value='sk-passe1-ficticia'), \
         patch.object(launcher.passe1, '_dir_config', return_value=tmp_path), \
         patch.object(launcher, '_catalogo', return_value={'glm-5.3-flash': {'name': 'modelo'}}):
        with patch.object(win_image_adapter, 'preparar_imagens', side_effect=preparado), \
             patch.object(launcher, '_supervision', side_effect=OSError('falha motor')):
            with pytest.raises(ValueError, match='iniciar o motor'):
                launcher.iniciar([], agente=args)
        with patch.object(win_image_adapter, 'preparar_imagens', side_effect=falha_limpeza), \
             patch.object(launcher, '_supervision', return_value=0):
            with pytest.raises(ValueError, match='preparar ou limpar'):
                launcher.iniciar([], agente=args)
