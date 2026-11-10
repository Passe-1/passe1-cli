"""Verifica colagem real no terminal e integridade dos anexos no gateway."""
from __future__ import annotations

import importlib.util
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pyte

spec = importlib.util.spec_from_file_location('smoke_engine', Path(__file__).with_name('smoke-engine.py'))
modulo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modulo)


def testar(pasta, ambiente, comandos, esperado, prompt):
    if os.name == 'nt':
        from winpty import PtyProcess
        processo = PtyProcess.spawn([sys.executable, '-m', 'passe1'], cwd=pasta, env=ambiente, dimensions=(30, 100))
        ler = processo.read
        enviar = processo.write
        fechar = lambda: processo.close(force=True)
        pausas = ()
        fins = (EOFError, OSError)
    else:
        import pexpect
        processo = pexpect.spawn(sys.executable, ['-m', 'passe1'], cwd=pasta, env=ambiente, encoding='utf-8', dimensions=(30, 100), timeout=1)
        ler = lambda: processo.read_nonblocking(size=8192, timeout=1)
        enviar = processo.send
        fechar = lambda: processo.close(force=True)
        pausas = (pexpect.TIMEOUT,)
        fins = (pexpect.EOF, EOFError, OSError)
    fila = queue.Queue()
    def receber():
        while True:
            try:
                fila.put(ler())
            except pausas:
                continue
            except fins:
                fila.put(None)
                return
    threading.Thread(target=receber, daemon=True).start()
    tela = pyte.Screen(100, 30)
    fluxo = pyte.Stream(tela)
    indice = 0
    prazo = time.monotonic() + 90
    try:
        while time.monotonic() < prazo:
            try:
                trecho = fila.get(timeout=1)
            except queue.Empty:
                continue
            if trecho is None:
                break
            fluxo.feed(trecho)
            visivel = '\n'.join(tela.display)
            if indice < len(comandos):
                gatilho, texto = comandos[indice]
                if gatilho in visivel:
                    enviar(texto)
                    indice += 1
            relevantes = [req for req in modulo.Gateway.requisicoes if prompt in json.dumps(req, ensure_ascii=False)]
            if relevantes:
                corpo = json.dumps(relevantes, ensure_ascii=False)
                if esperado not in corpo:
                    raise RuntimeError('Gateway recebeu conteúdo incompleto da colagem.')
                if indice != len(comandos):
                    raise RuntimeError('Conteúdo foi enviado antes de revisar o anexo.')
                enviar('\x04')
                return
        raise RuntimeError('Composer não concluiu teste no terminal: ' + '\n'.join(tela.display)[-2000:])
    finally:
        fechar()


def main():
    servidor = ThreadingHTTPServer(('127.0.0.1', 0), modulo.Gateway)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='passe1-composer-') as pasta:
            config = Path(pasta) / 'config'
            config.mkdir()
            ambiente = {**os.environ, 'TERM': 'xterm-256color', 'PASSE1_CONFIG_DIR': str(config), 'PASSE1_BASE_URL': f'http://127.0.0.1:{servidor.server_port}/v1'}
            subprocess.run([sys.executable, '-I', '-c', "import passe1; passe1._guardar_chave('sk-passe1-fixture')"], cwd=pasta, env=ambiente, check=True, timeout=30)
            modulo.Gateway.requisicoes.clear()
            testar(pasta, ambiente, [('Enter enviar', 'prefixo \x1b[200~linha um\nlinha dois\x1b[201~\r')], 'prefixo linha um\\nlinha dois', 'prefixo')
            arquivo = Path(pasta) / 'arquivo com espaços.txt'
            arquivo.write_text('CONTEUDO_ANEXO_PTY', encoding='utf-8')
            modulo.Gateway.requisicoes.clear()
            testar(pasta, ambiente, [
                ('Enter enviar', '\x1b[200~' + str(arquivo) + '\x1b[201~'),
                ('Anexo preparado', '/anexos\r'),
                ('1. arquivo com espaços.txt', 'Analise o arquivo.\r'),
            ], 'CONTEUDO_ANEXO_PTY', 'Analise o arquivo.')
            print('Composer preservou colagem multilinha e anexo revisado no pseudoterminal.')
    finally:
        servidor.shutdown()
        servidor.server_close()


if __name__ == '__main__':
    main()
