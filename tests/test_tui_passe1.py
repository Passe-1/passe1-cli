import asyncio

import httpx

from passe1_engine.tui import Interface


def test_interacoes_concorrentes_respeitam_ordem_e_tipo():
    chamadas = []

    async def testar():
        async def receber(req):
            chamadas.append((req.url.path, req.read()))
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(receber), base_url='http://127.0.0.1') as cliente:
            interface = Interface(cliente, 'modelo', 'ses_raiz', [])
            await interface.evento({'type': 'question.asked', 'properties': {
                'id': 'q1', 'sessionID': 'ses_raiz', 'questions': [{'question': 'Aceita a sugestão?', 'options': []}],
            }})
            await interface.evento({'type': 'permission.asked', 'properties': {
                'id': 'p1', 'sessionID': 'ses_raiz', 'permission': 'bash', 'patterns': ['touch arquivo'],
            }})
            assert 'Aceita a sugestão?' in interface.transcricao.text
            assert 'Permissão necessária' not in interface.transcricao.text
            await interface.enviar('sim')
            assert chamadas[0][0] == '/question/q1/reply'
            assert b'answers' in chamadas[0][1]
            assert 'Permissão necessária' in interface.transcricao.text
            await interface.enviar('n')
            assert chamadas[1][0] == '/permission/p1/reply'
            assert b'reject' in chamadas[1][1]

    asyncio.run(testar())
