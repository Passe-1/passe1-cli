"""Abre a TUI instalada em pseudoterminal e conversa com gateway simulado."""
from __future__ import annotations

import importlib.util
import os
import queue
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location("smoke_engine", Path(__file__).with_name("smoke-engine.py"))
modulo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modulo)


def testar_terminal(pasta, ambiente):
    if os.name == "nt":
        from winpty import PtyProcess
        processo = PtyProcess.spawn([sys.executable, "-m", "passe1"], cwd=pasta, env=ambiente, dimensions=(30, 100))
        ler = processo.read
        enviar = lambda texto: processo.write(texto)
        fechar = lambda: processo.close(force=True)
        tempos = ()
        encerramentos = (EOFError, OSError)
    else:
        import pexpect
        processo = pexpect.spawn(sys.executable, ["-m", "passe1"], cwd=pasta, env=ambiente, encoding="utf-8", dimensions=(30, 100), timeout=90)
        ler = lambda: processo.read_nonblocking(size=4096, timeout=1)
        enviar = processo.send
        fechar = lambda: processo.close(force=True)
        tempos = (pexpect.TIMEOUT,)
        encerramentos = (pexpect.EOF, EOFError, OSError)

    mensagens = queue.Queue()
    def receber():
        while True:
            try:
                mensagens.put(ler())
            except tempos:
                continue
            except encerramentos:
                mensagens.put(None)
                return
    leitor = threading.Thread(target=receber, daemon=True)
    leitor.start()
    texto = ""
    prazo = time.monotonic() + 120
    enviado = False
    try:
        while time.monotonic() < prazo:
            try:
                trecho = mensagens.get(timeout=1)
            except queue.Empty:
                continue
            if trecho is None:
                break
            texto = (texto + trecho)[-200000:]
            if not enviado and ("commands" in texto.lower() or "comandos" in texto.lower()):
                enviar("Responda ao teste.\r")
                enviado = True
            if enviado and "PASSE1_TUI_SUCESSO" in texto:
                return
        raise RuntimeError("TUI não concluiu a conversa simulada dentro do prazo.")
    finally:
        fechar()


def main():
    anterior = Path.cwd()
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), modulo.Gateway)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as pasta:
            try:
                config = Path(pasta) / "config"
                config.mkdir()
                (config / "key").write_text("sk-passe1-fixture\n", encoding="utf-8")
                ambiente = os.environ.copy()
                ambiente.update({"PASSE1_CONFIG_DIR": str(config), "PASSE1_BASE_URL": f"http://127.0.0.1:{servidor.server_port}/v1"})
                modulo.Gateway.autorizada = False
                testar_terminal(pasta, ambiente)
                if not modulo.Gateway.autorizada:
                    raise RuntimeError("Gateway simulado não recebeu autenticação.")
                print("TUI respondeu via gateway simulado em pseudoterminal.")
            finally:
                os.chdir(anterior)
    finally:
        servidor.shutdown()
        servidor.server_close()


if __name__ == "__main__":
    main()
