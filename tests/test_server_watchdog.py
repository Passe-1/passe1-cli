import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="O guardião de grupo é POSIX.")
GUARDIAO = Path(__file__).resolve().parents[1] / "passe1_engine" / "server_watchdog.py"


def test_fim_abrupto_do_servidor_interrompe_grupo(tmp_path):
    marcador = tmp_path / "sobreviveu"
    pronto = tmp_path / "iniciado"
    supervisor = """
import os, subprocess, sys, time
leitura, escrita = os.pipe()
comando = subprocess.Popen([sys.executable, sys.argv[2], str(leitura), sys.executable, '-c', 'import pathlib,signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); pathlib.Path(sys.argv[2]).touch(); time.sleep(3); pathlib.Path(sys.argv[1]).touch()', sys.argv[1], sys.argv[3]], pass_fds=(leitura,), start_new_session=True)
os.close(leitura)
while not os.path.exists(sys.argv[3]):
    if comando.poll() is not None: raise RuntimeError("comando encerrou antes de iniciar")
    time.sleep(0.02)
print(comando.pid, flush=True)
time.sleep(30)
"""
    pai = subprocess.Popen([sys.executable, "-c", supervisor, str(marcador), str(GUARDIAO), str(pronto)], stdout=subprocess.PIPE, text=True)
    grupo = None
    try:
        grupo = int(pai.stdout.readline())
        os.kill(pai.pid, signal.SIGKILL)
        pai.wait(timeout=5)
        time.sleep(4)
        assert not marcador.exists()
    finally:
        if pai.poll() is None:
            pai.kill()
            pai.wait()
        if grupo:
            try:
                os.killpg(grupo, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_comando_normal_preserva_saida_e_codigo():
    leitura, escrita = os.pipe()
    try:
        resultado = subprocess.run([sys.executable, str(GUARDIAO), str(leitura), sys.executable, "-c", "print('ok'); raise SystemExit(7)"], check=False, pass_fds=(leitura,), capture_output=True, text=True, timeout=5)
        assert resultado.returncode == 7
        assert resultado.stdout == "ok\n"
    finally:
        os.close(leitura)
        os.close(escrita)


def test_fim_normal_interrompe_descendentes_em_background(tmp_path):
    marcador = tmp_path / "sobreviveu"
    leitura, escrita = os.pipe()
    comando = "(sleep 2; touch \"$1\") &"
    try:
        resultado = subprocess.run([sys.executable, str(GUARDIAO), str(leitura), "/bin/sh", "-c", comando, "sh", str(marcador)], check=False, pass_fds=(leitura,), capture_output=True, text=True, timeout=5)
        assert resultado.returncode == 0
        time.sleep(3)
        assert not marcador.exists()
    finally:
        os.close(leitura)
        os.close(escrita)


def test_sinal_durante_criacao_encerra_filho(tmp_path):
    marcador = tmp_path / "sobreviveu"
    leitura, escrita = os.pipe()
    injetor = """
import os, runpy, signal, subprocess, sys
original = subprocess.Popen
def iniciar(*args, **kwargs):
    os.kill(os.getpid(), signal.SIGTERM)
    return original(*args, **kwargs)
subprocess.Popen = iniciar
sys.argv = [sys.argv[1], sys.argv[2], sys.executable, '-c', 'import pathlib,sys,time; time.sleep(2); pathlib.Path(sys.argv[1]).touch()', sys.argv[3]]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    try:
        resultado = subprocess.run([sys.executable, "-c", injetor, str(GUARDIAO), str(leitura), str(marcador)], check=False, pass_fds=(leitura,), capture_output=True, text=True, timeout=5)
        assert resultado.returncode == 128 + signal.SIGTERM
        time.sleep(3)
        assert not marcador.exists()
    finally:
        os.close(leitura)
        os.close(escrita)
