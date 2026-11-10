"""Mata servidor real durante ferramenta e confere encerramento do grupo POSIX."""
import importlib.util
import json
import os
import shlex
import signal
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx

spec = importlib.util.spec_from_file_location("smoke_engine", Path(__file__).with_name("smoke-engine.py"))
modulo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modulo)


class Gateway(modulo.Gateway):
    comando = ""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_longo", "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": self.comando, "description": "Teste de encerramento", "timeout": 30000})}}]}
        eventos = [
            {"id": "test", "object": "chat.completion.chunk", "model": "glm-5.3-flash", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"id": "test", "object": "chat.completion.chunk", "model": "glm-5.3-flash", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        ]
        dados = ("".join("data: " + json.dumps(evento) + "\n\n" for evento in eventos) + "data: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)


def main():
    if os.name == "nt":
        print("Teste POSIX ignorado no Windows.")
        return
    servidor_gateway = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=servidor_gateway.serve_forever, daemon=True).start()
    anterior = Path.cwd()
    processo = None
    grupo = None
    try:
        with tempfile.TemporaryDirectory() as pasta:
            os.chdir(pasta)
            raiz = Path(pasta)
            pronto, marcador = raiz / "pronto", raiz / "sobreviveu"
            codigo = "import os,pathlib,signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(5); pathlib.Path(sys.argv[2]).touch()"
            Gateway.comando = " ".join(map(shlex.quote, [sys.executable, "-c", codigo, str(pronto), str(marcador)]))
            os.environ["PASSE1_CONFIG_DIR"] = str(raiz / "config")
            os.environ["PASSE1_BASE_URL"] = f"http://127.0.0.1:{servidor_gateway.server_port}/v1"
            import passe1
            from passe1_engine import launcher, tui

            passe1._guardar_chave("sk-passe1-fixture")
            capturado = {}
            original = tui.iniciar
            try:
                tui.iniciar = lambda binario, env, modelo, **_kwargs: capturado.update(binario=str(binario), env=env)
                launcher.iniciar([])
            finally:
                tui.iniciar = original
            config = json.loads(capturado["env"]["OPENCODE_CONFIG_CONTENT"])
            config["permission"]["bash"] = "allow"
            config["permission"]["external_directory"] = "allow"
            capturado["env"]["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
            processo, url, segredo = tui._servidor(capturado["binario"], capturado["env"])
            with httpx.Client(base_url=url, auth=("opencode", segredo), trust_env=False, timeout=20) as cliente:
                resposta = cliente.post("/session", json={})
                resposta.raise_for_status()
                sessao = resposta.json()["id"]
                resposta = cliente.post(f"/session/{sessao}/prompt_async", json={"model": {"providerID": "passe1", "modelID": "glm-5.3-flash"}, "parts": [{"type": "text", "text": "Execute a ferramenta do teste."}]})
                resposta.raise_for_status()
                limite = time.monotonic() + 45
                while not pronto.exists() and processo.poll() is None and time.monotonic() < limite:
                    time.sleep(0.05)
                if not pronto.exists():
                    raise RuntimeError("Ferramenta não iniciou antes do prazo.")
                grupo = os.getpgid(int(pronto.read_text()))
                processo.kill()
                processo.wait(timeout=10)
                time.sleep(6)
                if marcador.exists():
                    raise RuntimeError("Filho sobreviveu à morte abrupta do servidor.")
                print("Servidor real interrompido: processo de ferramenta encerrado.")
    finally:
        if processo is not None and processo.poll() is None:
            processo.kill()
            processo.wait(timeout=10)
        if grupo is not None:
            try:
                os.killpg(grupo, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        os.chdir(anterior)
        servidor_gateway.shutdown()
        servidor_gateway.server_close()


if __name__ == "__main__":
    main()
