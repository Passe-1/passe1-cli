"""E2E do consumidor do motor instalado contra imagem alterada por mmap."""
import hashlib
import json
import mmap
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='requer Windows')


def test_motor_instalado_recusa_imagem_alterada_antes_do_post(tmp_path):
    class Gateway(BaseHTTPRequestHandler):
        posts = 0

        def log_message(self, *_args):
            pass

        def do_GET(self):
            dados = json.dumps({'data': [{'id': 'glm-5.3-flash', 'tipo': 'chat', 'vision': True}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def do_POST(self):
            type(self).posts += 1
            self.send_response(500)
            self.end_headers()

    servidor = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    ambiente = os.environ.copy()
    ambiente['PASSE1_CONFIG_DIR'] = str(tmp_path / 'config')
    ambiente['PASSE1_BASE_URL'] = f'http://127.0.0.1:{servidor.server_port}/v1'
    programa = '''
import json
import passe1
from passe1_engine import launcher
passe1._guardar_chave('sk-passe1-fixture')
capturado = {}
launcher._executar_motor = lambda args, env: capturado.update(binario=args[0], env=env)
launcher.iniciar([])
print(json.dumps(capturado))
'''
    imagem = tmp_path / 'foto.png'
    original = b'\x89PNG\r\n\x1a\nconteudo-teste'
    imagem.write_bytes(original)
    try:
        preparado = subprocess.run([sys.executable, '-I', '-c', programa], cwd=tmp_path, env=ambiente, capture_output=True, text=True, encoding='utf-8', timeout=60, check=True)
        dados = json.loads(preparado.stdout.splitlines()[-1])
        dados['env']['PASSE1_IMAGE_HASHES'] = json.dumps({str(imagem): hashlib.sha256(original).hexdigest()})
        with imagem.open('r+b') as arquivo:
            with mmap.mmap(arquivo.fileno(), 0, access=mmap.ACCESS_WRITE) as mapa:
                mapa[-1] = ord('X')
                mapa.flush()
                resultado = subprocess.run([dados['binario'], 'run', '--pure', '--format', 'json', '-m', 'passe1/glm-5.3-flash', 'Analise a imagem.', '--file', str(imagem)], cwd=tmp_path, env=dados['env'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120, check=False)
        saida = resultado.stdout + resultado.stderr
        assert resultado.returncode != 0
        assert 'imagem' in saida.lower() and ('mudou' in saida.lower() or 'diverg' in saida.lower() or 'recus' in saida.lower())
        assert Gateway.posts == 0
    finally:
        servidor.shutdown()
        servidor.server_close()
