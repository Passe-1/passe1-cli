"""Testa o binário instalado contra gateway local simulado, sem rede externa."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar


class Gateway(BaseHTTPRequestHandler):
    autorizada = False
    requisicoes: ClassVar[list] = []

    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "glm-5.3-flash", "tipo": "chat", "vision": True}]}).encode())

    def do_POST(self):
        type(self).autorizada = self.headers.get("Authorization") == "Bearer sk-passe1-fixture"
        dados_entrada = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            type(self).requisicoes.append(json.loads(dados_entrada))
        except ValueError:
            pass
        eventos = [
            {"id": "test", "object": "chat.completion.chunk", "model": "glm-5.3-flash", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "PASSE1_TUI_SUCESSO"}, "finish_reason": None}]},
            {"id": "test", "object": "chat.completion.chunk", "model": "glm-5.3-flash", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        corpo = "".join("data: " + json.dumps(e) + "\n\n" for e in eventos) + "data: [DONE]\n\n"
        dados = corpo.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)


def main():
    anterior = Path.cwd()
    with tempfile.TemporaryDirectory() as pasta:
        os.chdir(pasta)
        try:
            executar(pasta)
        finally:
            os.chdir(anterior)


def executar(pasta):
    os.environ["PASSE1_CONFIG_DIR"] = str(Path(pasta) / "config")
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    os.environ["PASSE1_BASE_URL"] = f"http://127.0.0.1:{servidor.server_port}/v1"
    import passe1
    from passe1_engine import launcher

    passe1._guardar_chave("sk-passe1-fixture")
    exec_original = launcher._executar_motor
    capturado = {}
    try:
        launcher._executar_motor = lambda args, env: capturado.update(binario=args[0], env=env)
        launcher.iniciar([])
    finally:
        launcher._executar_motor = exec_original
    resultado = subprocess.run([capturado["binario"], "run", "--pure", "--format", "json", "-m", "passe1/glm-5.3-flash", "Responda OK."], env=capturado["env"], capture_output=True, text=True, timeout=120, check=False)
    servidor.shutdown()
    servidor.server_close()
    eventos = []
    for linha in resultado.stdout.splitlines():
        try:
            eventos.append(json.loads(linha))
        except ValueError:
            continue
    if resultado.returncode != 0 or not Gateway.autorizada or not any("PASSE1_TUI_SUCESSO" in str(e.get("part", {}).get("text", "")) for e in eventos):
        raise SystemExit("Falhou comunicação com gateway simulado: " + resultado.stderr[-500:])
    if "PASSE1_API_KEY" in capturado["env"]:
        raise SystemExit("A chave não deve ser herdada pelo ambiente do motor.")
    print("Motor instalado respondeu via gateway simulado; autenticação confirmada.")

if __name__ == "__main__":
    main()
