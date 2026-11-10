"""Confirma que bytes trocados no staging nunca são enviados ao gateway."""
from __future__ import annotations

import base64
import importlib.util
import os
import tempfile
import threading
from argparse import Namespace
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('smoke_engine', Path(__file__).with_name('smoke-engine.py'))
modulo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modulo)


def main():
    anterior = Path.cwd()
    with tempfile.TemporaryDirectory() as pasta:
        raiz = Path(pasta)
        arquivo = raiz / 'foto.png'
        arquivo.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC'))
        servidor = ThreadingHTTPServer(('127.0.0.1', 0), modulo.Gateway)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        try:
            os.chdir(raiz)
            os.environ['PASSE1_CONFIG_DIR'] = str(raiz / 'config')
            os.environ['PASSE1_BASE_URL'] = f'http://127.0.0.1:{servidor.server_port}/v1'
            import passe1
            from passe1_engine import launcher
            passe1._guardar_chave('sk-passe1-fixture')
            modulo.Gateway.requisicoes.clear()
            original = launcher._supervision
            chamadas = []
            def adulterar(comando, ambiente):
                chamadas.append(True)
                alvo = Path(comando[comando.index('--file') + 1])
                alvo.write_bytes(b'\x89PNG\r\n\x1a\nSEGREDO_TROCADO')
                return original(comando, ambiente)
            args = Namespace(base_url=None, model='glm-5.3-flash', session=None, sandbox=False, image=[str(arquivo)], tarefa=['analise'])
            with patch.object(launcher, '_supervision', side_effect=adulterar):
                codigo = launcher.iniciar([], agente=args)
            if not chamadas or codigo == 0 or modulo.Gateway.requisicoes:
                raise SystemExit('Falhou: imagem adulterada foi aceita ou enviada ao gateway.')
            print('Imagem adulterada recusada antes de chegar ao gateway.')
        finally:
            servidor.shutdown()
            servidor.server_close()
            os.chdir(anterior)


if __name__ == '__main__':
    main()
