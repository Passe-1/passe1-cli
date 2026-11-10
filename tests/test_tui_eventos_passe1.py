import asyncio

import httpx

from passe1_engine.tui import Interface


def executar(corrotina):
    return asyncio.run(corrotina)


def test_fim_normal_do_stream_bloqueia_envio_e_descarta_pendencias():
    async def teste():
        async def responder(request):
            return httpx.Response(200, content=b'data: {"type":"server.connected","properties":{}}\n\n')
        async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(responder)) as cliente:
            interface = Interface(cliente, 'modelo', 'ses_raiz', [])
            interface.interacoes.append(('permissao', {'id': 'per_1'}))
            await interface.eventos()
            assert interface.estado == 'Desconectado'
            assert not interface.interacoes
            await interface.enviar('olá')
            assert 'Reinicie o Passe1' in interface.transcricao.text
    executar(teste())


def test_resposta_externa_remove_somente_interacao_correspondente():
    async def teste():
        interface = Interface(None, 'modelo', 'ses_raiz', [])
        primeiro = {'id': 'per_1', 'sessionID': 'ses_raiz', 'permission': 'bash'}
        segundo = {'id': 'que_2', 'sessionID': 'ses_raiz', 'questions': [{'question': 'Qual?', 'options': []}]}
        interface.interacoes = [('permissao', primeiro), ('pergunta', segundo)]
        await interface.evento({'type': 'permission.replied', 'properties': {'sessionID': 'ses_outra', 'requestID': 'per_1'}})
        assert len(interface.interacoes) == 2
        await interface.evento({'type': 'permission.replied', 'properties': {'sessionID': 'ses_raiz', 'requestID': 'per_1'}})
        assert interface.interacoes == [('pergunta', segundo)]
        assert 'Qual?' in interface.transcricao.text
        await interface.evento({'type': 'question.rejected', 'properties': {'sessionID': 'ses_raiz', 'requestID': 'que_2'}})
        assert not interface.interacoes
    executar(teste())


def test_evento_resposta_antes_do_retorno_http_nao_remove_proxima_interacao():
    async def teste():
        interface = None
        async def responder(request):
            await interface.evento({'type': 'permission.replied', 'properties': {'sessionID': 'ses_raiz', 'requestID': 'per_1'}})
            return httpx.Response(200, json=True)
        async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(responder)) as cliente:
            interface = Interface(cliente, 'modelo', 'ses_raiz', [])
            primeiro = {'id': 'per_1', 'sessionID': 'ses_raiz', 'permission': 'bash'}
            segundo = {'id': 'que_2', 'sessionID': 'ses_raiz', 'questions': [{'question': 'Qual?', 'options': []}]}
            interface.interacoes = [('permissao', primeiro), ('pergunta', segundo)]
            await interface.enviar('sim')
            assert interface.interacoes == [('pergunta', segundo)]
    executar(teste())


def test_abort_descarta_pedidos_sem_evento_de_resposta():
    async def teste():
        async def responder(request):
            return httpx.Response(200, json=True)
        async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(responder)) as cliente:
            interface = Interface(cliente, 'modelo', 'ses_raiz', [])
            interface.interacoes.append(('permissao', {'id': 'per_1', 'sessionID': 'ses_raiz'}))
            await interface.abortar()
            assert not interface.interacoes
    executar(teste())


def test_imagem_anexada_preserva_bytes_validados(tmp_path):
    import base64

    async def teste():
        imagem = tmp_path / 'foto com espaços.png'
        original = b'\x89PNG\r\n\x1a\nconteudo'
        imagem.write_bytes(original)
        interface = Interface(None, 'modelo', 'ses_raiz', [])
        await interface.enviar('/imagem ' + str(imagem))
        imagem.write_bytes(b'\x89PNG\r\n\x1a\noutro')
        url = interface.imagens[0]['url']
        assert base64.b64decode(url.split(',', 1)[1]) == original
        assert interface.imagens[0]['mime'] == 'image/png'
    executar(teste())


def test_rodape_mostra_todos_atalhos_em_terminal_estreito():
    from types import SimpleNamespace

    interface = Interface(None, 'modelo', 'ses_raiz', [])
    for largura in (20, 40, 80):
        interface.app = SimpleNamespace(output=SimpleNamespace(get_size=lambda largura=largura: SimpleNamespace(columns=largura)))
        linhas = interface._linhas_rodape()
        assert all(len(linha) <= largura - 1 for linha in linhas)
        texto = ' '.join(linhas)
        for atalho in ('Enter enviar', 'Ctrl+O linha', 'Esc parar', 'Ctrl+D sair'):
            assert atalho in texto


def test_supervisor_windows_cria_e_fecha_job(monkeypatch):
    import io
    from types import SimpleNamespace

    from passe1_engine import tui, win_job

    eventos = []

    class Processo:
        stdout = io.StringIO('opencode server listening on http://127.0.0.1:8123\n')
        def poll(self):
            return None
        def terminate(self):
            eventos.append('terminate')
        def wait(self, timeout=None):
            eventos.append('wait')

    processo = Processo()
    def iniciar(comando, **kwargs):
        eventos.append('iniciar_job')
        assert comando[1] == 'serve'
        assert kwargs['env']['OPENCODE_SERVER_PASSWORD']
        return processo

    monkeypatch.setattr(tui, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(win_job, 'iniciar', iniciar)
    monkeypatch.setattr(win_job, 'fechar', lambda processo: eventos.append('fechar_job'))
    resultado, url, _ = tui._servidor('motor.exe', {})
    assert resultado is processo
    assert url == 'http://127.0.0.1:8123'
    tui._encerrar_servidor(processo)
    assert eventos == ['iniciar_job', 'terminate', 'wait', 'fechar_job']


def test_supervisor_windows_fecha_job_quando_servidor_falha(monkeypatch):
    import io
    from types import SimpleNamespace

    import pytest

    from passe1_engine import tui, win_job

    eventos = []
    processo = SimpleNamespace(stdout=io.StringIO(''), poll=lambda: 1)
    monkeypatch.setattr(tui, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(win_job, 'iniciar', lambda *args, **kwargs: processo)
    monkeypatch.setattr(win_job, 'fechar', lambda processo: eventos.append('fechar_job'))
    with pytest.raises(RuntimeError):
        tui._servidor('motor.exe', {})
    assert eventos == ['fechar_job']
