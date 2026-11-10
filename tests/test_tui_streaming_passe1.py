"""Exibição distingue texto da resposta de partes internas do modelo."""
import asyncio

from passe1_engine.tui import Interface


def test_stream_oculta_raciocinio_e_exibe_resposta_sem_duplicar():
    async def testar():
        interface = Interface(None, 'modelo', 'ses_raiz', [])
        await interface.evento({'type': 'message.updated', 'properties': {'info': {'id': 'msg1', 'sessionID': 'ses_raiz', 'role': 'assistant'}}})
        async def parte(id_, tipo, texto):
            await interface.evento({'type': 'message.part.updated', 'properties': {'part': {'id': id_, 'messageID': 'msg1', 'sessionID': 'ses_raiz', 'type': tipo, 'text': texto}}})
        async def delta(id_, texto):
            await interface.evento({'type': 'message.part.delta', 'properties': {'sessionID': 'ses_raiz', 'messageID': 'msg1', 'partID': id_, 'field': 'text', 'delta': texto}})
        await delta('desconhecido', 'NÃO MOSTRAR')
        await parte('razao', 'reasoning', '')
        await delta('razao', 'THOUGHT SECRET')
        await parte('razao', 'reasoning', 'THOUGHT SECRET')
        await parte('resposta', 'text', '')
        await delta('resposta', 'Olá')
        assert 'Olá' in ''.join(interface.linhas)
        await delta('resposta', ', mundo!')
        await parte('resposta', 'text', 'Olá, mundo!')
        texto = ''.join(interface.linhas)
        assert 'NÃO MOSTRAR' not in texto
        assert 'THOUGHT SECRET' not in texto
        assert texto.count('Olá, mundo!') == 1
    asyncio.run(testar())


def test_historico_restaura_texto_sem_revelar_raciocinio(monkeypatch):
    from types import SimpleNamespace

    import httpx

    from passe1_engine import tui

    exibido = []

    class FalsaInterface:
        def __init__(self, *_args):
            self.conectado = asyncio.Event()
            self.ocupado = False
            self.app = SimpleNamespace(run_async=self.executar)
        def _adicionar(self, texto):
            exibido.append(texto)
        async def eventos(self):
            self.conectado.set()
            await asyncio.Event().wait()
        async def executar(self):
            return None

    def resposta(request):
        assert request.url.path == '/session/ses_raiz/message'
        return httpx.Response(200, json=[{'info': {'role': 'assistant'}, 'parts': [
            {'type': 'reasoning', 'text': 'RACIOCINIO PRIVADO'},
            {'type': 'text', 'text': 'Resposta restaurada'},
        ]}])

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(resposta), base_url='http://localhost')
    monkeypatch.setattr(tui.httpx, 'AsyncClient', lambda **_kwargs: cliente)
    monkeypatch.setattr(tui, 'Interface', FalsaInterface)
    assert asyncio.run(tui._executar('http://localhost', 'segredo', 'modelo', 'ses_raiz', [], '')) == 0
    assert 'Resposta restaurada' in ''.join(exibido)
    assert 'RACIOCINIO PRIVADO' not in ''.join(exibido)
