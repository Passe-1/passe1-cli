"""E2E de recuperação interativa Windows com pacote instalado."""
import os
import queue
import subprocess
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='requer Windows')


def test_recuperar_no_terminal_sem_expor_conteudo(tmp_path):
    from winpty import PtyProcess

    config = tmp_path / 'config'
    alvo = tmp_path / 'arquivo.txt'
    segredo = 'conteúdo confidencial de teste'
    alvo.write_text(segredo, encoding='utf-8')
    ambiente = os.environ.copy()
    ambiente['PASSE1_CONFIG_DIR'] = str(config)
    programa = '''
import os, sys
from passe1_engine import win_secure as w
original = w._gravar_handle
def interromper(kernel, handle, texto):
    original(kernel, handle, 'parcial')
    os._exit(91)
w._gravar_handle = interromper
w.substituir(sys.argv[1], sys.argv[2], 'novo', identidade=w.snapshot(sys.argv[1])['identidade'])
'''
    resultado = subprocess.run([sys.executable, '-c', programa, str(alvo), segredo], cwd=tmp_path, env=ambiente, check=False, timeout=30)
    assert resultado.returncode == 91
    assert alvo.read_text(encoding='utf-8') == 'parcial'
    proc = PtyProcess.spawn([sys.executable, '-m', 'passe1', 'recover', str(alvo)], cwd=str(tmp_path), env=ambiente, dimensions=(30, 100))
    mensagens = queue.Queue()
    def ler():
        try:
            while True:
                mensagens.put(proc.read())
        except (EOFError, OSError):
            mensagens.put(None)
    threading.Thread(target=ler, daemon=True).start()
    recebido, confirmado = '', False
    limite = time.monotonic() + 30
    try:
        while time.monotonic() < limite:
            try:
                parte = mensagens.get(timeout=1)
            except queue.Empty:
                continue
            if parte is None:
                break
            recebido += parte
            if not confirmado and 'Confirmar recuperação?' in recebido:
                proc.write('s\r')
                confirmado = True
            if 'Arquivo recuperado.' in recebido:
                break
    finally:
        proc.close(force=True)
    assert confirmado and 'Arquivo recuperado.' in recebido
    assert segredo not in recebido
    assert alvo.read_text(encoding='utf-8') == segredo
