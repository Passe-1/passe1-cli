import base64
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import passe1

PNG = b'\x89PNG\r\n\x1a\nimagem'
JPEG = b'\xff\xd8\xffimagem'


@pytest.fixture(autouse=True)
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    monkeypatch.setenv('PASSE1_BASE_URL', 'https://gateway.teste/v1')
    passe1._guardar_chave(passe1.PREFIXO_CHAVE + 'ficticia')


def test_chat_envia_imagens_com_mime_detectado_por_conteudo(tmp_path, monkeypatch):
    a, b = tmp_path / 'foto.txt', tmp_path / 'outra.jpg'
    a.write_bytes(PNG)
    b.write_bytes(JPEG)
    recebidas = []
    def handler(request):
        recebidas.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})
    monkeypatch.setattr(passe1, '_transporte', httpx.MockTransport(handler))
    assert passe1.main(['chat', '--image', str(a), '--image', str(b), 'descreva']) == 0
    partes = recebidas[0]['messages'][-1]['content']
    assert partes[0] == {'type': 'text', 'text': 'descreva'}
    assert partes[1]['image_url']['url'] == 'data:image/png;base64,' + base64.b64encode(PNG).decode()
    assert partes[2]['image_url']['url'].startswith('data:image/jpeg;base64,')


def test_agente_envia_imagem_na_primeira_mensagem(tmp_path, monkeypatch):
    imagem = tmp_path / 'figura.png'
    imagem.write_bytes(PNG)
    recebidas = []
    def handler(request):
        recebidas.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': 'ok'}}]})
    monkeypatch.setattr(passe1, '_transporte', httpx.MockTransport(handler))
    assert passe1.main(['agent', '--image', str(imagem), 'descreva']) == 0
    assert recebidas[0]['messages'][-1]['content'][1]['type'] == 'image_url'


def test_formato_invalido_nao_envia_requisicao(tmp_path, monkeypatch, capsys):
    imagem = tmp_path / 'falso.png'
    imagem.write_bytes(b'nao e imagem')
    monkeypatch.setattr(passe1, '_transporte', httpx.MockTransport(lambda _: pytest.fail('enviou imagem inválida')))
    assert passe1.main(['chat', '--image', str(imagem), 'oi']) == 1
    assert 'Formato de imagem inválido' in capsys.readouterr().out


def test_limite_total_entre_imagens(tmp_path, monkeypatch, capsys):
    imagem = tmp_path / 'grande.png'
    imagem.write_bytes(PNG + b'x' * (4 * 1024 * 1024))
    monkeypatch.setattr(passe1, '_transporte', httpx.MockTransport(lambda _: pytest.fail('enviou imagem grande')))
    assert passe1.main(['agent', '--image', str(imagem), 'oi']) == 1
    assert 'limite total de 4 MiB' in capsys.readouterr().out


def test_imagem_ausente_retorna_erro(tmp_path, capsys):
    assert passe1.main(['chat', '--image', str(tmp_path / 'ausente'), 'oi']) == 1
    assert 'Não consegui ler a imagem' in capsys.readouterr().out


def test_chat_interativo_anexa_imagem_uma_vez_ao_historico(tmp_path, monkeypatch):
    imagem = tmp_path / 'figura.png'
    imagem.write_bytes(PNG)
    recebidas = []
    def handler(request):
        recebidas.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})
    entradas = iter(['primeira', 'segunda', '/sair'])
    monkeypatch.setattr(passe1, '_transporte', httpx.MockTransport(handler))
    monkeypatch.setattr('builtins.input', lambda _: next(entradas))
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    assert passe1.main(['chat', '--image', str(imagem)]) == 0
    mensagens = recebidas[1]['messages']
    usuarios = [m for m in mensagens if m['role'] == 'user']
    assert usuarios[0]['content'][1]['type'] == 'image_url'
    assert usuarios[1]['content'] == 'segunda'


def test_agente_interativo_anexa_imagem_na_primeira_tarefa(tmp_path, monkeypatch):
    imagem = tmp_path / 'figura.png'
    imagem.write_bytes(PNG)
    recebidas = []
    def handler(request):
        recebidas.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': 'ok'}}]})
    entradas = iter(['descreva', '/sair'])
    monkeypatch.setattr(passe1, '_transporte', httpx.MockTransport(handler))
    monkeypatch.setattr('builtins.input', lambda _: next(entradas))
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    assert passe1.main(['agent', '--image', str(imagem)]) == 0
    assert recebidas[0]['messages'][-1]['content'][1]['type'] == 'image_url'
