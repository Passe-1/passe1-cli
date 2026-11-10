import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('vendor_lock', RAIZ / 'scripts' / 'vendor_lock.py')
modulo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modulo)


def test_trava_bloqueia_outro_processo_e_libera_ao_terminar(tmp_path):
    pronto = tmp_path / 'pronto'
    programa = '''
import importlib.util, pathlib, sys, time
spec = importlib.util.spec_from_file_location('lock', sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
with m.segurar(sys.argv[2]):
    pathlib.Path(sys.argv[3]).write_text('pronto')
    time.sleep(30)
'''
    filho = subprocess.Popen([sys.executable, '-c', programa, str(RAIZ / 'scripts' / 'vendor_lock.py'), str(RAIZ), str(pronto)])
    try:
        prazo = time.monotonic() + 10
        while not pronto.exists() and time.monotonic() < prazo:
            time.sleep(0.05)
        assert pronto.exists()
        with pytest.raises(TimeoutError):
            with modulo.segurar(RAIZ, timeout=0.2):
                pass
    finally:
        filho.kill()
        filho.wait(timeout=10)
    with modulo.segurar(RAIZ, timeout=2):
        assert os.path.isdir(RAIZ / 'vendor')
