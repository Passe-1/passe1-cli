"""Valida opções legadas de imagem e sessão no motor instalado, sem rede externa."""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import zlib
from http.server import ThreadingHTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location("smoke_engine", Path(__file__).with_name("smoke-engine.py"))
modulo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modulo)


def png():
    def chunk(tipo, dados):
        return len(dados).to_bytes(4, "big") + tipo + dados + (zlib.crc32(tipo + dados) & 0xffffffff).to_bytes(4, "big")
    cabecalho = (1).to_bytes(4, "big") * 2 + bytes([8, 2, 0, 0, 0])
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", cabecalho) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")


def executar(pasta, ambiente, imagem, primeira):
    argumentos = ["agent", "--session", "teste", "--image", str(imagem), "Pergunta inicial"] if primeira else ["agent", "--session", "teste", "Segunda pergunta"]
    if os.name == "nt":
        comando = [sys.executable, "-m", "passe1", *argumentos]
    else:
        # Exercita o mesmo adaptador que é selecionado no Windows.
        codigo = "import sys,passe1; from passe1_engine import launcher; a=passe1.montar_parser().parse_args(sys.argv[1:]); raise SystemExit(launcher.iniciar([], agente=a))"
        comando = [sys.executable, "-c", codigo, *argumentos]
    return subprocess.run(comando, cwd=pasta, env=ambiente, capture_output=True, text=True, timeout=120, check=False)


def main():
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), modulo.Gateway)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as pasta:
            config = Path(pasta) / "config"
            config.mkdir()
            (config / "key").write_text("sk-passe1-fixture\n", encoding="utf-8")
            imagem = Path(pasta) / "figura.png"
            imagem.write_bytes(png())
            ambiente = os.environ.copy()
            ambiente.update({"PASSE1_CONFIG_DIR": str(config), "PASSE1_BASE_URL": f"http://127.0.0.1:{servidor.server_port}/v1"})
            modulo.Gateway.autorizada = False
            modulo.Gateway.requisicoes = []
            for primeira in (True, False):
                resultado = executar(pasta, ambiente, imagem, primeira)
                if resultado.returncode != 0 or "PASSE1_TUI_SUCESSO" not in resultado.stdout:
                    raise RuntimeError(
                        f"Agente não concluiu: etapa={'imagem' if primeira else 'retomada'}, "
                        f"código={resultado.returncode}, posts={len(modulo.Gateway.requisicoes)}, "
                        f"stdout={resultado.stdout[-1000:]!r}, stderr={resultado.stderr[-1000:]!r}"
                    )
            corpos = modulo.Gateway.requisicoes
            if not modulo.Gateway.autorizada or not any("image_url" in json.dumps(c) for c in corpos):
                raise RuntimeError("Gateway não recebeu a imagem autenticada. Estrutura: " + repr([(c.get("model"), [(m.get("role"), [(part.get("type"), list(part.keys())) for part in m.get("content", []) if isinstance(part, dict)] if isinstance(m.get("content"), list) else "texto") for m in c.get("messages", [])]) for c in corpos]))
            if not any("Segunda pergunta" in json.dumps(c) and "PASSE1_TUI_SUCESSO" in json.dumps(c) for c in corpos):
                db_path = next(config.rglob("*.db"))
                db = sqlite3.connect(str(db_path))
                sessoes = db.execute("SELECT title, directory FROM session").fetchall()
                db.close()
                raise RuntimeError("A segunda chamada não retomou o histórico anterior. " + repr(sessoes))
            print("Sessão e imagem do agente funcionaram via gateway simulado.")
    finally:
        servidor.shutdown()
        servidor.server_close()


if __name__ == "__main__":
    main()
