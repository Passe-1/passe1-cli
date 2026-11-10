"""Login, saldo e logout por HTTP local com gateway simulado."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

import passe1


class Gateway(BaseHTTPRequestHandler):
    aprovado = False
    encerrado = False

    def log_message(self, *_args):
        pass

    def resposta(self, codigo, corpo, cookie=False):
        dados = json.dumps(corpo).encode()
        self.send_response(codigo)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(dados)))
        if cookie:
            self.send_header('Set-Cookie', 'passe1_session=fixture; Path=/; HttpOnly')
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        if self.headers.get('Cookie') != 'passe1_session=fixture':
            return self.resposta(401, {})
        if self.path == '/api/me':
            return self.resposta(200, {'usuario': {'email': 'teste@example.com'}, 'credito': {'saldo_cents': 100, 'bonus_cents': 0}, 'chaves': [{'id': '1', 'masked': 'sk-...1234', 'status': 'ativa'}]})
        if self.path == '/api/me/saldo':
            return self.resposta(200, {'saldo_centavos': 100, 'bonus_centavos': 0})
        return self.resposta(404, {})

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}')
        if self.path == '/api/cli/iniciar':
            return self.resposta(200, {'browser_code': 'a' * 43, 'device_code': 'b' * 43, 'expires_in': 600})
        if self.path == '/api/cli/autorizar':
            type(self).aprovado = corpo.get('code') == 'a' * 43
            return self.resposta(200 if self.aprovado else 400, {})
        if self.path == '/api/cli/token':
            if corpo.get('device_code') != 'b' * 43 or not self.aprovado:
                return self.resposta(202, {'status': 'pending'})
            return self.resposta(200, {'status': 'authorized'}, cookie=True)
        if self.path in ('/api/keys/reveal', '/api/contas/logout') and self.headers.get('Cookie') != 'passe1_session=fixture':
            return self.resposta(401, {})
        if self.path == '/api/keys/reveal':
            return self.resposta(200, {'key': 'sk-passe1-fixture'})
        if self.path == '/api/contas/logout':
            type(self).encerrado = True
            return self.resposta(200, {'ok': True})
        return self.resposta(404, {})


def test_login_saldo_chave_e_logout_por_http(tmp_path, monkeypatch, capsys):
    Gateway.aprovado = Gateway.encerrado = False
    servidor = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{servidor.server_port}'
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path))
    monkeypatch.setenv('PASSE1_BASE_URL', base + '/v1')
    monkeypatch.setattr(passe1, '_transporte', None)
    monkeypatch.setattr('builtins.input', lambda _: '1')
    def aprovar(_url):
        request = Request(base + '/api/cli/autorizar', data=json.dumps({'code': 'a' * 43}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        with urlopen(request, timeout=5) as resposta:
            return resposta.status == 200
    monkeypatch.setattr(passe1.webbrowser, 'open', aprovar)
    try:
        assert passe1.main(['login']) == 0
        assert passe1._ler_chave() == 'sk-passe1-fixture'
        assert passe1.main(['status']) == 0
        assert 'R$ 1,00' in capsys.readouterr().out
        assert passe1.main(['logout']) == 0
        assert Gateway.encerrado and passe1._ler_sessao() is None and passe1._ler_chave() is None
    finally:
        servidor.shutdown()
        servidor.server_close()
        thread.join(timeout=5)
