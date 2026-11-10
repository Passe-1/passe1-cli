"""Exercita grep do wheel com chamada de ferramenta e gateway simulado."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch


class Gateway(BaseHTTPRequestHandler):
    respostas: ClassVar[list] = []

    def log_message(self, *_args):
        pass

    def do_GET(self):
        dados = json.dumps({'data': [{'id': 'glm-5.3-flash', 'tipo': 'chat'}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_POST(self):
        requisicao = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        mensagens = requisicao.get('messages', [])
        resultados = [item for item in mensagens if item.get('role') == 'tool']
        self.respostas.extend(resultados)
        if resultados:
            delta = {'role': 'assistant', 'content': 'BUSCA_CONCLUIDA'}
            final = 'stop'
        else:
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_grep', 'type': 'function', 'function': {'name': 'grep', 'arguments': json.dumps({'pattern': 'BUSCA_SEGURA_FIXTURE|SEGREDO_FIXTURE'})}}]}
            final = 'tool_calls'
        partes = [
            {'id': 'test', 'object': 'chat.completion.chunk', 'model': 'glm-5.3-flash', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
            {'id': 'test', 'object': 'chat.completion.chunk', 'model': 'glm-5.3-flash', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': final}]},
        ]
        dados = (''.join('data: ' + json.dumps(item) + '\n\n' for item in partes) + 'data: [DONE]\n\n').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)


def main():
    original = Path.cwd()
    with tempfile.TemporaryDirectory() as pasta:
        raiz = Path(pasta) / 'projeto'
        raiz.mkdir()
        (raiz / 'publico.txt').write_text('BUSCA_SEGURA_FIXTURE', encoding='utf-8')
        (raiz / '.env').write_text('SEGREDO_FIXTURE', encoding='utf-8')
        os.link(raiz / '.env', raiz / 'alias.txt')
        os.chdir(raiz)
        servidor = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        try:
            os.environ['PASSE1_CONFIG_DIR'] = str(Path(pasta) / 'config')
            os.environ['PASSE1_BASE_URL'] = f'http://127.0.0.1:{servidor.server_port}/v1'
            import passe1
            from passe1_engine import launcher
            passe1._guardar_chave('sk-passe1-fixture')
            capturado = {}
            with patch('passe1_engine.tui.iniciar', side_effect=lambda binario, env, modelo: capturado.update(binary=str(binario), env=env)):
                launcher.iniciar([])
            ambiente = capturado['env']
            if os.name != 'nt':
                ambiente['PATH'] = '/usr/bin:/bin'
            resultado = subprocess.run([capturado['binary'], 'run', '--pure', '--format', 'json', '-m', 'passe1/glm-5.3-flash', 'Procure BUSCA_SEGURA_FIXTURE.'], env=ambiente, capture_output=True, text=True, timeout=120, check=False)
            saida = json.dumps(Gateway.respostas, ensure_ascii=False)
            if resultado.returncode or 'BUSCA_CONCLUIDA' not in resultado.stdout or 'BUSCA_SEGURA_FIXTURE' not in saida or 'SEGREDO_FIXTURE' in saida:
                raise SystemExit('Smoke do grep falhou: ' + resultado.stderr[-1000:] + ' ' + resultado.stdout[-1000:] + ' retorno=' + saida[-1000:])
            print('Grep do wheel usou rg empacotado e não expôs arquivo sensível.')
        finally:
            servidor.shutdown()
            servidor.server_close()
            os.chdir(original)


if __name__ == '__main__':
    main()
