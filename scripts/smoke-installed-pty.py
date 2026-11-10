"""Exercita a PTY do motor instalado, incluindo entrada e saída pelo websocket."""
from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from websockets.sync.client import connect


def main():
    import passe1_engine

    motor = Path(passe1_engine.__file__).parent / "bin" / ("opencode.exe" if os.name == "nt" else "opencode")
    with tempfile.TemporaryDirectory(prefix="passe1-pty-") as pasta:
        with socket.socket() as porta:
            porta.bind(("127.0.0.1", 0))
            numero = porta.getsockname()[1]
        base = f"http://127.0.0.1:{numero}"
        senha = os.urandom(24).hex()
        auth = "Basic " + base64.b64encode(f"opencode:{senha}".encode()).decode()
        ambiente = os.environ.copy()
        ambiente.update({
            "OPENCODE_SERVER_PASSWORD": senha,
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_DISABLE_MODELS_FETCH": "1",
            "OPENCODE_CONFIG_CONTENT": '{"formatter":false,"lsp":false}',
            **{f"XDG_{nome}_HOME": str(Path(pasta) / nome.lower()) for nome in ("CONFIG", "DATA", "CACHE", "STATE")},
        })
        headers = {"Authorization": auth, "x-opencode-directory": pasta}

        def requisitar(caminho, dados=None, extras=None):
            cabecalhos = {**headers, **(extras or {})}
            corpo = None
            if dados is not None:
                corpo = json.dumps(dados).encode()
                cabecalhos["Content-Type"] = "application/json"
            req = urllib.request.Request(base + caminho, data=corpo, headers=cabecalhos)
            with urllib.request.urlopen(req, timeout=10) as resposta:
                return json.load(resposta)

        with tempfile.TemporaryFile(mode="w+") as log:
            processo = subprocess.Popen([str(motor), "serve", "--hostname", "127.0.0.1", "--port", str(numero)], cwd=pasta, env=ambiente, stdout=log, stderr=subprocess.STDOUT)
            try:
                prazo = time.monotonic() + 45
                while True:
                    try:
                        requisitar("/global/health")
                        break
                    except (OSError, urllib.error.URLError):
                        if processo.poll() is not None or time.monotonic() >= prazo:
                            log.seek(0)
                            raise RuntimeError("Motor não iniciou: " + log.read()[-2000:]) from None
                        time.sleep(0.1)
                criado = requisitar("/api/pty", {"command": sys.executable, "args": ["-u", "-c", "input(); print('PASSE1_PTY_RESPOSTA', flush=True); input()"]})
                ident = criado["data"]["id"]
                token = requisitar(f"/api/pty/{ident}/connect-token", {}, {"x-opencode-ticket": "1"})["data"]["ticket"]
                with connect(f"ws://127.0.0.1:{numero}/api/pty/{ident}/connect?ticket={token}", additional_headers=headers, open_timeout=15) as canal:
                    enter = "\r" if os.name == "nt" else "\n"
                    canal.send("iniciar" + enter)
                    texto = ""
                    prazo = time.monotonic() + 30
                    while "PASSE1_PTY_RESPOSTA" not in texto:
                        restante = prazo - time.monotonic()
                        if restante <= 0:
                            raise RuntimeError("PTY não retornou a resposta esperada")
                        try:
                            quadro = canal.recv(timeout=restante)
                        except TimeoutError:
                            estado = requisitar(f"/api/pty/{ident}")["data"]["status"]
                            raise RuntimeError(f"PTY não respondeu: estado={estado}, saída={texto[-500:]!r}") from None
                        texto += quadro.decode("utf-8", "replace") if isinstance(quadro, bytes) else quadro
                    canal.send("encerrar" + enter)
                prazo = time.monotonic() + 15
                while requisitar(f"/api/pty/{ident}")["data"]["status"] != "exited":
                    if time.monotonic() >= prazo:
                        raise RuntimeError("Processo da PTY não encerrou")
                    time.sleep(0.1)
                print("Motor instalado abriu PTY e confirmou entrada e saída.")
            finally:
                processo.terminate()
                try:
                    processo.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    processo.kill()
                    processo.wait()


if __name__ == "__main__":
    main()
