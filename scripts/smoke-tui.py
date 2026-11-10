"""Abre a TUI instalada em pseudoterminal e conversa com gateway simulado."""
from __future__ import annotations

import base64
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


def testar_terminal(pasta, ambiente, argumentos=(), aguardar_imagem=False):
    if os.name == "nt":
        from winpty import PtyProcess
        processo = PtyProcess.spawn([sys.executable, "-m", "passe1", *argumentos], cwd=pasta, env=ambiente, dimensions=(30, 100))
        ler = processo.read
        enviar = lambda texto: processo.write(texto)
        fechar = lambda: processo.close(force=True)
        tempos = ()
        encerramentos = (EOFError, OSError)
    else:
        import pexpect
        processo = pexpect.spawn(sys.executable, ["-m", "passe1", *argumentos], cwd=pasta, env=ambiente, encoding="utf-8", dimensions=(30, 100), timeout=90)
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
            if not enviado and ("commands" in texto.lower() or "comandos" in texto.lower()) and (not aguardar_imagem or "[Image 1]" in texto):
                enviar("Responda ao teste.\r")
                enviado = True
            if enviado and "PASSE1_TUI_SUCESSO" in texto:
                return
        raise RuntimeError("TUI não concluiu a conversa simulada dentro do prazo: " + texto[-4000:])
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
                temporarios = Path(pasta) / "temporarios"
                temporarios.mkdir()
                ambiente["TMPDIR"] = str(temporarios)
                ambiente.update({"PASSE1_CONFIG_DIR": str(config), "PASSE1_BASE_URL": f"http://127.0.0.1:{servidor.server_port}/v1"})
                modulo.Gateway.autorizada = False
                testar_terminal(pasta, ambiente)
                if not modulo.Gateway.autorizada:
                    raise RuntimeError("Gateway simulado não recebeu autenticação.")
                imagem = Path(pasta) / "imagem.png"
                imagem.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"))
                modulo.Gateway.requisicoes.clear()
                testar_terminal(pasta, ambiente, ["agent", "--model", "glm-5.3-flash", "--session", "teste", "--image", str(imagem)], aguardar_imagem=True)
                if not any("image_url" in str(item) or "data:image/png" in str(item) for item in modulo.Gateway.requisicoes):
                    raise RuntimeError("TUI não enviou a imagem inicial ao gateway simulado.")
                if list(temporarios.glob("passe1-cli-images-*")):
                    raise RuntimeError("Staging de imagem não foi removido ao encerrar a TUI.")
                if sys.platform == "darwin":
                    testar_terminal(pasta, ambiente, ["agent", "--sandbox"])
                print("TUI respondeu via gateway simulado em pseudoterminal.")
            finally:
                os.chdir(anterior)
    finally:
        servidor.shutdown()
        servidor.server_close()


if __name__ == "__main__":
    main()
