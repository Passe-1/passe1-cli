"""E2E local de move transacional e recuperação confirmada no wheel."""
from __future__ import annotations

import json
import os
import subprocess
import sys
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
        recebido = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        resultados = [item for item in recebido.get('messages', []) if item.get('role') == 'tool']
        self.respostas.extend(resultados)
        if resultados:
            delta, fim = {'role': 'assistant', 'content': 'MOVE_CONCLUIDO'}, 'stop'
        else:
            patch_text = '*** Begin Patch\n*** Update File: origem.txt\n*** Move to: destino.txt\n@@\n-origem-ficticia\n+resultado-ficticio\n*** End Patch'
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_move', 'type': 'function', 'function': {'name': 'apply_patch', 'arguments': json.dumps({'patchText': patch_text})}}]}
            fim = 'tool_calls'
        partes = [
            {'id': 'test', 'object': 'chat.completion.chunk', 'model': 'glm-5.3-flash', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
            {'id': 'test', 'object': 'chat.completion.chunk', 'model': 'glm-5.3-flash', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': fim}]},
        ]
        corpo = (''.join('data: ' + json.dumps(item) + '\n\n' for item in partes) + 'data: [DONE]\n\n').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


def main(destino_existe=True):
    if os.name != 'posix':
        raise SystemExit('Move transacional Windows ainda não está disponível.')
    import pexpect
    anterior = Path.cwd()
    with tempfile.TemporaryDirectory() as pasta:
        raiz = Path(pasta).resolve()
        projeto = raiz / 'projeto'
        projeto.mkdir()
        origem, destino = projeto / 'origem.txt', projeto / 'destino.txt'
        origem.write_text('origem-ficticia\n', encoding='utf-8')
        if destino_existe:
            destino.write_text('destino-confidencial-ficticio\n', encoding='utf-8')
        servidor = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        try:
            os.chdir(projeto)
            os.environ['PASSE1_CONFIG_DIR'] = str(raiz / 'config')
            os.environ['PASSE1_BASE_URL'] = f'http://127.0.0.1:{servidor.server_port}/v1'
            import passe1
            from passe1_engine import launcher
            passe1._guardar_chave('sk-passe1-fixture')
            capturado = {}
            with patch.object(launcher, '_executar_motor', side_effect=lambda args, env: capturado.update(binary=args[0], env=env)):
                launcher.iniciar([])
            ambiente = capturado['env']
            config = json.loads(ambiente['OPENCODE_CONFIG_CONTENT'])
            config['permission']['edit'] = 'allow'
            config['provider']['passe1']['models']['gpt-test'] = {'name': 'gpt-test', 'tool_call': True}
            ambiente['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)
            resultado = subprocess.run([capturado['binary'], 'run', '--pure', '--format', 'json', '-m', 'passe1/gpt-test', 'Execute o teste de move.'], cwd=projeto, env=ambiente, capture_output=True, text=True, timeout=120, check=False)
            retorno = json.dumps(Gateway.respostas, ensure_ascii=False)
            journals = list((raiz / 'config' / 'opencode' / 'move-journals').glob('.passe1-journal-*'))
            if resultado.returncode or 'MOVE_CONCLUIDO' not in resultado.stdout or len(journals) != 1 or 'destino-confidencial-ficticio' in retorno:
                raise RuntimeError(f'Move não concluiu: código={resultado.returncode}, journals={len(journals)}, retorno={retorno[-1200:]!r}, stdout={resultado.stdout[-1200:]!r}, stderr={resultado.stderr[-1200:]!r}')
            if origem.exists() or destino.read_text(encoding='utf-8') != 'resultado-ficticio\n':
                raise RuntimeError('Move produziu estado inesperado.')
            processo = pexpect.spawn(sys.executable, ['-I', '-m', 'passe1', 'recover-move', str(journals[0])], cwd=str(projeto), env=ambiente, encoding='utf-8', timeout=30)
            try:
                processo.expect('destino-confidencial-ficticio' if destino_existe else 'origem-ficticia')
                processo.expect('Restaurar o estado revisado')
                processo.sendline('sim')
                processo.expect('Recuperação concluída')
                processo.expect(pexpect.EOF)
            finally:
                processo.close(force=True)
            if origem.read_text(encoding='utf-8') != 'origem-ficticia\n':
                raise RuntimeError('Recovery não restaurou a origem.')
            if destino_existe:
                if destino.read_text(encoding='utf-8') != 'destino-confidencial-ficticio\n':
                    raise RuntimeError('Recovery não restaurou o destino.')
            elif destino.exists():
                raise RuntimeError('Recovery deixou o destino que antes estava ausente.')
            print('Move e recovery confirmados no wheel, sem expor backup ao modelo.')
        finally:
            servidor.shutdown()
            servidor.server_close()
            os.chdir(anterior)


if __name__ == '__main__':
    main()
