"""Recovery local de credenciais no pacote Windows instalado."""
import os
import queue
import subprocess
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='requer Windows e pywinpty')


def _terminal(arquivo, pasta, ambiente, resposta, esperado):
    from winpty import PtyProcess

    processo = PtyProcess.spawn([sys.executable, '-I', '-X', 'utf8', '-m', 'passe1', 'recover-credential', str(arquivo)], cwd=str(pasta), env=ambiente, dimensions=(30, 100))
    mensagens = queue.Queue()

    def ler():
        try:
            while True:
                mensagens.put(processo.read())
        except (EOFError, OSError):
            mensagens.put(None)

    threading.Thread(target=ler, daemon=True).start()
    texto, respondeu = '', False
    limite = time.monotonic() + 30
    try:
        while time.monotonic() < limite:
            try:
                parte = mensagens.get(timeout=1)
            except queue.Empty:
                continue
            if parte is None:
                break
            texto += parte
            if not respondeu and 'Confirmar recuperação?' in texto:
                processo.write(resposta + '\r')
                respondeu = True
            if respondeu and esperado in texto:
                break
    finally:
        processo.close(force=True)
    assert respondeu and esperado in texto
    assert 'credencial-anterior-ficticia' not in texto and 'credencial-nova-ficticia' not in texto


@pytest.mark.parametrize('existente', [False, True])
def test_recovery_de_credencial_no_wheel_exige_confirmacao(tmp_path, existente):
    config, arquivo = tmp_path / 'config', tmp_path / 'config' / 'key'
    ambiente = os.environ.copy()
    ambiente['PASSE1_CONFIG_DIR'] = str(config)
    programa = '''
import os, sys
from passe1_engine import win_credentials
arquivo, existente = sys.argv[1:]
if existente == '1':
    win_credentials.gravar(arquivo, b'credencial-anterior-ficticia')
gravar = win_credentials._gravar_handle
def interromper(kernel, handle, dados):
    gravar(kernel, handle, b'parcial')
    os._exit(91)
win_credentials._gravar_handle = interromper
win_credentials.gravar(arquivo, b'credencial-nova-ficticia')
'''
    resultado = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', programa, str(arquivo), '1' if existente else '0'], cwd=tmp_path, env=ambiente, capture_output=True, timeout=60, check=False)
    assert resultado.returncode == 91, resultado.stderr.decode('utf-8', errors='replace')[-1000:]
    journal = arquivo.with_name(arquivo.name + '.passe1-cred-journal')
    assert journal.exists() and arquivo.read_bytes() == b'parcial'
    comando = [sys.executable, '-I', '-X', 'utf8', '-m', 'passe1', 'recover-credential', str(arquivo)]
    sem_tty = subprocess.run(comando, cwd=tmp_path, env=ambiente, input='s\n', text=True, encoding='utf-8', capture_output=True, timeout=30, check=False)
    assert sem_tty.returncode != 0 and arquivo.read_bytes() == b'parcial'
    _terminal(arquivo, tmp_path, ambiente, 'n', 'Recuperação cancelada.')
    assert arquivo.read_bytes() == b'parcial' and journal.exists()
    _terminal(arquivo, tmp_path, ambiente, 's', 'Credencial recuperada.')
    if existente:
        assert arquivo.read_bytes() == b'credencial-anterior-ficticia'
    else:
        assert not arquivo.exists()
    assert not journal.exists()
