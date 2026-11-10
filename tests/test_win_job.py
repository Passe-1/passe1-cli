import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Job Object é específico do Windows.")


def test_servidor_suspenso_e_retomado():
    from passe1_engine import win_job

    processo = win_job.iniciar([sys.executable, "-c", "print('INICIO_OK')"], stdout=subprocess.PIPE, text=True)
    try:
        assert processo.wait(timeout=15) == 0
        assert processo.stdout.read().strip() == "INICIO_OK"
    finally:
        win_job.fechar(processo)


def test_morte_da_cli_encerra_servidor_e_descendente(tmp_path):
    marcador = tmp_path / "sobreviveu"
    pronto = tmp_path / "pronto"
    supervisor = """
import subprocess, sys, time
from passe1_engine import win_job
filho = 'import pathlib,sys,time; pathlib.Path(sys.argv[2]).touch(); time.sleep(4); pathlib.Path(sys.argv[1]).touch()'
servidor = 'import subprocess,sys,time; subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2], sys.argv[3]]); time.sleep(30)'
p = win_job.iniciar([sys.executable, '-c', servidor, filho, sys.argv[1], sys.argv[2]])
while not __import__('os').path.exists(sys.argv[2]):
    if p.poll() is not None: raise RuntimeError('servidor terminou antes do teste')
    time.sleep(.02)
print('PRONTO', flush=True)
time.sleep(30)
"""
    pai = subprocess.Popen([sys.executable, "-c", supervisor, str(marcador), str(pronto)], stdout=subprocess.PIPE, text=True)
    try:
        prazo = time.monotonic() + 20
        while not pronto.exists() and pai.poll() is None and time.monotonic() < prazo:
            time.sleep(0.05)
        assert pronto.exists(), "Servidor ou descendente não iniciou no prazo."
        pai.kill()
        pai.wait(timeout=10)
        time.sleep(5)
        assert not marcador.exists()
    finally:
        if pai.poll() is None:
            pai.kill()
            pai.wait(timeout=10)
