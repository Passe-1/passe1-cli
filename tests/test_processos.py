"""Captura limitada e encerramento do grupo para comandos normais."""
import importlib.util
import os
import subprocess
import sys
import time

import pytest


def _processos():
    assert importlib.util.find_spec("processos") is not None, "captura limitada ainda não implementada"
    import processos
    return processos


@pytest.mark.skipif(os.name != "posix", reason="ulimit e head são POSIX")
def test_limita_saida_sem_arquivo_temporario(tmp_path):
    processos = _processos()
    cmd = ["/bin/sh", "-c", "ulimit -f 8; set -e; head -c 1048576 /dev/zero; printf final"]
    code, output, timeout = processos.executar(cmd, tmp_path, limite=64)
    assert code == 0 and not timeout
    assert len(output) < 100 and "truncad" in output.lower()


@pytest.mark.skipif(os.name != "posix", reason="grupos de processos POSIX")
def test_retorno_normal_encerra_grupo_em_background(tmp_path):
    processos = _processos()
    code, output, timeout = processos.executar(["/bin/sh", "-c", "sleep 10 & echo $!"], tmp_path, timeout=2)
    assert code == 0 and not timeout
    pid = int(output.strip())
    time.sleep(0.1)
    ps = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
    assert ps.returncode != 0 or ps.stdout.strip().startswith("Z")


@pytest.mark.skipif(os.name != "posix", reason="grupos de processos POSIX")
def test_timeout_encerra_grupo(tmp_path):
    processos = _processos()
    code, output, timeout = processos.executar(["/bin/sh", "-c", "sleep 10 & echo $!; wait"], tmp_path, timeout=0.3)
    assert timeout and code is None
    pid = int(output.strip())
    time.sleep(0.1)
    ps = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
    assert ps.returncode != 0 or ps.stdout.strip().startswith("Z")


def test_captura_por_thread_limita_saida_e_preserva_codigo(tmp_path):
    processos = _processos()
    code, output, timeout = processos._executar_windows(
        [sys.executable, "-c", "import os; os.write(1,b'x'*1048576); raise SystemExit(7)"], tmp_path, 2, 64, False
    )
    assert code == 7 and not timeout
    assert len(output) < 100 and "truncad" in output.lower()


@pytest.mark.skipif(os.name != "posix", reason="fixture de shell POSIX")
def test_captura_por_thread_nao_aguarda_pipe_herdado(tmp_path):
    processos = _processos()
    inicio = time.monotonic()
    code, _, timeout = processos._executar_windows(
        ["/bin/sh", "-c", "sleep 2 & exit 0"], tmp_path, 3, 64, False
    )
    assert code == 0 and not timeout and time.monotonic() - inicio < 1.5


def test_aceita_string_no_shell_nativo(tmp_path):
    processos = _processos()
    code, output, timeout = processos.executar("echo passe1-shell", tmp_path, shell=True)
    assert code == 0 and not timeout and output.strip() == "passe1-shell"


def test_captura_por_thread_interrompe_processo_principal_no_timeout(tmp_path):
    processos = _processos()
    inicio = time.monotonic()
    code, _, timeout = processos._executar_windows(
        [sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, 0.2, 64, False
    )
    assert code is None and timeout and time.monotonic() - inicio < 1.5
