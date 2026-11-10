"""O comando aprovado permanece dentro dos limites declarados do sandbox."""
import importlib.util
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time

import pytest


def _sandbox():
    assert importlib.util.find_spec("sandbox") is not None, "sandbox ainda não implementado"
    import sandbox
    return sandbox


def test_plataforma_nao_suportada_recusa_sem_executar(tmp_path, monkeypatch):
    sandbox = _sandbox()
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="(?i)sandbox"):
        sandbox.executar("touch executou", tmp_path)
    assert not (tmp_path / "executou").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec é do macOS")
def test_proibe_escrita_no_projeto_fora_e_em_hardlink(tmp_path):
    sandbox = _sandbox()
    projeto = tmp_path / 'projeto "com aspas"'
    projeto.mkdir()
    fora = tmp_path / "fora"
    fora.mkdir()
    alvo = fora / "arquivo"
    alvo.write_text("intacto")
    os.link(alvo, projeto / "hardlink")
    (projeto / "atalho").symlink_to(fora, target_is_directory=True)
    code, _, timeout = sandbox.executar("printf dentro > dentro", projeto)
    assert code != 0 and not timeout and not (projeto / "dentro").exists()
    for caminho in (str(alvo), "atalho/arquivo", "hardlink"):
        code, _, timeout = sandbox.executar(f"printf alterado > {shlex.quote(caminho)}", projeto)
        assert code != 0 and not timeout and alvo.read_text() == "intacto"


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec é do macOS")
def test_bloqueia_rede_mesmo_local(tmp_path):
    sandbox = _sandbox()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        cmd = f"{shlex.quote(sys.executable)} -c 'import socket; socket.create_connection((\"127.0.0.1\", {port}), 1)'"
        code, _, timeout = sandbox.executar(cmd, tmp_path)
    assert code != 0 and not timeout


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec é do macOS")
def test_timeout_encerra_grupo_incluindo_processo_em_background(tmp_path):
    sandbox = _sandbox()
    code, output, timeout = sandbox.executar("sleep 10 & echo $!; wait", tmp_path, timeout=0.3)
    assert timeout and code is None
    pid = int(output.strip())
    time.sleep(0.1)
    ps = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
    assert ps.returncode != 0 or ps.stdout.strip().startswith("Z")


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec é do macOS")
def test_saida_grande_nao_grava_arquivo_temporario_sem_limite(tmp_path):
    sandbox = _sandbox()
    code, output, timeout = sandbox.executar("ulimit -f 8; set -e; head -c 1048576 /dev/zero; printf final", tmp_path, limite=64)
    assert code == 0 and not timeout
    assert len(output) < 100 and "truncad" in output.lower()


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec é do macOS")
def test_saida_e_codigo_sao_retornados_e_limitados(tmp_path):
    sandbox = _sandbox()
    code, output, timeout = sandbox.executar("printf 123456789; exit 7", tmp_path, limite=4)
    assert code == 7 and output.startswith("1234") and "truncad" in output.lower() and not timeout


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec é do macOS")
def test_bloqueia_conexao_por_socket_unix(tmp_path):
    sandbox = _sandbox()
    with tempfile.TemporaryDirectory(dir="/tmp") as pasta, socket.socket(socket.AF_UNIX) as listener:
        caminho = pasta + "/rede.sock"
        listener.bind(caminho)
        listener.listen()
        programa = f"import socket; s=socket.socket(socket.AF_UNIX); s.connect({caminho!r})"
        cmd = f"{shlex.quote(sys.executable)} -c {shlex.quote(programa)}"
        code, _, timeout = sandbox.executar(cmd, tmp_path)
    assert code != 0 and not timeout


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec é do macOS")
def test_fim_normal_encerra_processo_em_background(tmp_path):
    sandbox = _sandbox()
    code, output, timeout = sandbox.executar("sleep 10 & echo $!", tmp_path, timeout=2)
    assert code == 0 and not timeout
    pid = int(output.strip())
    time.sleep(0.1)
    ps = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
    assert ps.returncode != 0 or ps.stdout.strip().startswith("Z")


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec é do macOS")
def test_saida_de_filho_desacoplado_nao_prende_drenagem(tmp_path):
    sandbox = _sandbox()
    programa = (
        "import os,time; os.setsid(); print(os.getpid(),flush=True); "
        "fim=time.monotonic()+1.8; "
        "exec('while time.monotonic()<fim:\\n os.write(1,b\\\"x\\\")\\n time.sleep(0.001)')"
    )
    comando = f"{shlex.quote(sys.executable)} -c {shlex.quote(programa)} & sleep 0.2"
    inicio = time.monotonic()
    code, output, timeout = sandbox.executar(comando, tmp_path, timeout=0.6, limite=64)
    duracao = time.monotonic() - inicio
    assert code == 0 and not timeout
    assert output.strip() and duracao < 1.4
