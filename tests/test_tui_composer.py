import asyncio

import httpx
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from passe1_engine.tui import ComposerCompleter, Interface


def test_colagem_multilinha_nao_envia_automaticamente():
    async def executar():
        chamadas = []
        async def receber(req):
            chamadas.append(req)
            return httpx.Response(204)
        with create_pipe_input() as entrada, create_app_session(input=entrada, output=DummyOutput()):
            async with httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(receber)) as cliente:
                interface = Interface(cliente, 'teste', 'ses_teste', [])
                tarefa = asyncio.create_task(interface.app.run_async())
                while not interface.app.is_running:
                    await asyncio.sleep(0.001)
                entrada.send_text('\x1b[200~linha um\nlinha dois\n\x1b[201~')
                limite = asyncio.get_running_loop().time() + 2
                while interface.entrada.text != 'linha um\nlinha dois\n':
                    assert asyncio.get_running_loop().time() < limite
                    await asyncio.sleep(0.001)
                assert not chamadas
                interface.app.exit()
                await tarefa
    asyncio.run(executar())


def test_completa_comandos_e_caminhos(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'arquivo.txt').write_text('conteudo')
    completer = ComposerCompleter()
    assert '/anexos' in [x.text for x in completer.get_completions(Document('/an'), CompleteEvent())]
    assert 'quivo.txt' in [x.text for x in completer.get_completions(Document('/anexar ar'), CompleteEvent())]


def test_anexo_colado_revisado_removido_e_enviado(tmp_path):
    async def executar():
        arquivo = tmp_path / 'arquivo com espaço.txt'
        arquivo.write_text('conteudo anexo', encoding='utf-8')
        posts = []
        async def receber(req):
            posts.append(req.read())
            return httpx.Response(204)
        async with httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(receber)) as cliente:
            interface = Interface(cliente, 'teste', 'ses_teste', [])
            await interface.colar(str(arquivo))
            assert len(interface.anexos) == 1
            assert not posts
            await interface.enviar('/anexos')
            assert arquivo.name in interface.transcricao.text
            await interface.enviar('/remover 1')
            assert not interface.anexos
            await interface.enviar('/anexar ' + str(arquivo))
            await interface.enviar('analise')
            assert b'conteudo anexo' in posts[0]
            assert not interface.anexos
    asyncio.run(executar())


def test_historico_recupera_prompt_enviado_pelo_teclado():
    async def executar():
        async def receber(req):
            return httpx.Response(204)
        with create_pipe_input() as entrada, create_app_session(input=entrada, output=DummyOutput()):
            async with httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(receber)) as cliente:
                interface = Interface(cliente, 'teste', 'ses_teste', [])
                tarefa = asyncio.create_task(interface.app.run_async())
                while not interface.app.is_running:
                    await asyncio.sleep(0.001)
                entrada.send_text('prompt anterior\r')
                limite = asyncio.get_running_loop().time() + 2
                while not interface.ocupado:
                    assert asyncio.get_running_loop().time() < limite
                    await asyncio.sleep(0.001)
                entrada.send_text('\x1b[A')
                while interface.entrada.text != 'prompt anterior':
                    assert asyncio.get_running_loop().time() < limite
                    await asyncio.sleep(0.001)
                interface.app.exit()
                await tarefa
    asyncio.run(executar())


def test_paste_e_enter_no_mesmo_lote_envia_texto_completo():
    async def executar():
        posts = []
        async def receber(req):
            posts.append(req.read())
            return httpx.Response(204)
        with create_pipe_input() as entrada, create_app_session(input=entrada, output=DummyOutput()):
            async with httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(receber)) as cliente:
                interface = Interface(cliente, 'teste', 'ses_teste', [])
                tarefa = asyncio.create_task(interface.app.run_async())
                while not interface.app.is_running:
                    await asyncio.sleep(0.001)
                entrada.send_text('prefixo \x1b[200~COLADO\x1b[201~\r')
                limite = asyncio.get_running_loop().time() + 2
                while not posts:
                    assert asyncio.get_running_loop().time() < limite
                    await asyncio.sleep(0.001)
                assert b'prefixo COLADO' in posts[0]
                assert not interface.entrada.text
                interface.app.exit()
                await tarefa
    asyncio.run(executar())


def test_clipboard_lento_bloqueia_envio_e_preserva_texto(monkeypatch):
    import threading

    from passe1_engine import attachments
    pronto, liberar = threading.Event(), threading.Event()
    def ler():
        pronto.set()
        liberar.wait(2)
        return attachments.Anexo('clipboard.png', {'type': 'file', 'url': 'data:image/png;base64,YQ=='}, 1)
    monkeypatch.setattr(attachments, 'ler_clipboard', ler)
    async def executar():
        posts = []
        async def receber(req):
            posts.append(req.read())
            return httpx.Response(204)
        async with httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(receber)) as cliente:
            interface = Interface(cliente, 'teste', 'ses_teste', [])
            captura = asyncio.create_task(interface.colar_clipboard())
            while not pronto.is_set():
                await asyncio.sleep(0.001)
            await interface.enviar('analise')
            assert not posts
            assert interface.entrada.text == 'analise'
            liberar.set()
            await captura
            assert len(interface.anexos) == 1
    asyncio.run(executar())


def test_consentimento_sensivel_recusa_arquivo_trocado(tmp_path):
    async def executar():
        arquivo = tmp_path / '.env'
        arquivo.write_text('SEGREDO_ANTIGO')
        async with httpx.AsyncClient(base_url='http://localhost') as cliente:
            interface = Interface(cliente, 'teste', 'ses_teste', [])
            await interface.anexar(str(arquivo))
            assert interface.anexo_pendente
            token = interface.anexo_pendente[0]
            assert not interface.anexos
            assert 'SEGREDO_ANTIGO' not in interface.transcricao.text
            arquivo.unlink()
            arquivo.write_text('SEGREDO_NOVO')
            await interface.enviar('/confirmar-anexo ' + token)
            assert not interface.anexos
            assert interface.anexo_pendente is None
            assert 'SEGREDO_NOVO' not in interface.transcricao.text
    asyncio.run(executar())
