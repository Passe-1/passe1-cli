"""Exercita apply_patch e recovery Windows no wheel, com gateway simulado."""
from __future__ import annotations

import importlib.util
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from winpty import PtyProcess

spec = importlib.util.spec_from_file_location("smoke_move_recovery", Path(__file__).with_name("smoke-move-recovery.py"))
modulo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modulo)
Gateway = modulo.Gateway


def recuperar(journal, projeto, ambiente, destino_existe):
    processo = PtyProcess.spawn([sys.executable, "-I", "-m", "passe1", "recover-move", str(journal)], cwd=str(projeto), env=ambiente, dimensions=(30, 100))
    mensagens = queue.Queue()

    def ler():
        try:
            while True:
                mensagens.put(processo.read())
        except (EOFError, OSError):
            mensagens.put(None)

    threading.Thread(target=ler, daemon=True).start()
    texto = ""
    confirmado = False
    prazo = time.monotonic() + 45
    try:
        while time.monotonic() < prazo:
            try:
                parte = mensagens.get(timeout=1)
            except queue.Empty:
                continue
            if parte is None:
                break
            texto += parte
            if not confirmado and "Restaurar o estado revisado?" in texto:
                esperado = "destino-confidencial-ficticio" if destino_existe else "origem-ficticia"
                if esperado not in texto or "origem.txt" not in texto or "destino.txt" not in texto:
                    raise RuntimeError("Recovery não mostrou prévia completa antes da confirmação")
                processo.write("s\r")
                confirmado = True
            if confirmado and "Recuperação concluída" in texto:
                return
    finally:
        processo.close(force=True)
    raise RuntimeError(f"Recovery não concluiu: {texto[-2000:]!r}")


def executar(destino_existe):
    import passe1
    from passe1_engine import launcher

    Gateway.respostas.clear()
    with tempfile.TemporaryDirectory(prefix="passe1-move-e2e-") as pasta:
        raiz = Path(pasta).resolve()
        projeto = raiz / "projeto"
        projeto.mkdir()
        origem, destino = projeto / "origem.txt", projeto / "destino.txt"
        origem.write_text("origem-ficticia\n", encoding="utf-8")
        if destino_existe:
            destino.write_text("destino-confidencial-ficticio\n", encoding="utf-8")
        servidor = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        anterior = Path.cwd()
        try:
            os.chdir(projeto)
            os.environ["PASSE1_CONFIG_DIR"] = str(raiz / "config")
            os.environ["PASSE1_BASE_URL"] = f"http://127.0.0.1:{servidor.server_port}/v1"
            passe1._guardar_chave("sk-passe1-fixture")
            capturado = {}
            with patch.object(launcher, "_executar_motor", side_effect=lambda args, env: capturado.update(binario=args[0], env=env)):
                launcher.iniciar([])
            ambiente = capturado["env"]
            config = json.loads(ambiente["OPENCODE_CONFIG_CONTENT"])
            config["permission"]["edit"] = "allow"
            config["provider"]["passe1"]["models"]["gpt-test"] = {"name": "gpt-test", "tool_call": True}
            ambiente["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
            resultado = subprocess.run([capturado["binario"], "run", "--pure", "--format", "json", "-m", "passe1/gpt-test", "Execute o teste de move."], cwd=projeto, env=ambiente, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
            respostas = json.dumps(Gateway.respostas, ensure_ascii=False)
            journals = list((raiz / "config" / "opencode" / "move-journals").glob("move-*.journal"))
            if resultado.returncode or "MOVE_CONCLUIDO" not in resultado.stdout or len(journals) != 1 or "destino-confidencial-ficticio" in respostas:
                raise RuntimeError(f"Move falhou: código={resultado.returncode}, journals={len(journals)}, respostas={respostas[-1200:]!r}, stdout={resultado.stdout[-1200:]!r}, stderr={resultado.stderr[-1200:]!r}")
            if origem.exists() or destino.read_text(encoding="utf-8") != "resultado-ficticio\n":
                raise RuntimeError("Move não produziu o estado esperado")
            recuperar(journals[0], projeto, ambiente, destino_existe)
            if origem.read_text(encoding="utf-8") != "origem-ficticia\n":
                raise RuntimeError("Recovery não restaurou a origem")
            if destino_existe and destino.read_text(encoding="utf-8") != "destino-confidencial-ficticio\n":
                raise RuntimeError("Recovery não restaurou o destino")
            if not destino_existe and destino.exists():
                raise RuntimeError("Recovery não removeu o destino criado")
            print(f"Move e recovery pelo wheel passaram: destino {'existente' if destino_existe else 'ausente'}.")
        finally:
            servidor.shutdown()
            servidor.server_close()
            os.chdir(anterior)


if __name__ == "__main__":
    if os.name != "nt":
        raise SystemExit("Este smoke requer Windows e pywinpty.")
    executar(destino_existe=True)
    executar(destino_existe=False)
