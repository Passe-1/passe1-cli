"""Compila o OpenCode fixado para a plataforma local usando o Bun exigido."""
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from vendor_lock import segurar

raiz = Path(__file__).resolve().parents[1]
with segurar(raiz, timeout=300):
    fonte = raiz / "vendor" / "opencode"
    if not (fonte / "package.json").is_file():
        raise SystemExit("Inicialize o submódulo: git submodule update --init --recursive")
    sistema = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    arquitetura = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(platform.machine().lower())
    if not sistema or not arquitetura:
        raise SystemExit("Plataforma ainda não suportada pelo build.")
    bun_exigido = "1.4.3" if (sistema, arquitetura) == ("windows", "arm64") else "1.3.14"
    if not shutil.which("bun"):
        raise SystemExit(f"Instale Bun {bun_exigido} antes de compilar o motor.")
    versao = subprocess.check_output(["bun", "--version"], text=True).strip()
    if versao != bun_exigido:
        raise SystemExit(f"Bun {bun_exigido} é necessário; instalado: {versao}.")
    match = re.search(r'^VERSAO = "([^"]+)"', (raiz / "passe1.py").read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise SystemExit("Versão do Passe1 não encontrada.")
    alvo_rg = f"{('macos' if sistema == 'darwin' else sistema)}-{arquitetura}"
    bin_dir = raiz / "passe1_engine" / "bin"
    subprocess.run([sys.executable, str(raiz / "scripts" / "fetch-rg.py"), "--target", alvo_rg, "--output", str(bin_dir)], check=True)
    rg = bin_dir / ("rg.exe" if os.name == "nt" else "rg")
    ambiente = os.environ.copy()
    journal_temp = tempfile.TemporaryDirectory(prefix="passe1-build-journal-")
    ambiente["PASSE1_MOVE_ROOT"] = str(Path(journal_temp.name).resolve() / "journals")
    ambiente.update({"PASSE1_BUNDLED_RG": str(rg), "PASSE1_SECURE_PYTHON": sys.executable, "PASSE1_SECURE_ROOT": str(raiz), "OPENCODE_VERSION": match.group(1), "OPENCODE_CHANNEL": "passe1"})
    projeto = fonte / "packages" / "opencode"
    patch = raiz / "patches" / "opencode-hardlinks.patch"
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=fonte, check=True)
    subprocess.run(["git", "apply", str(patch)], cwd=fonte, check=True)
    try:
        subprocess.run(["bun", "install", "--frozen-lockfile"], cwd=fonte, check=True)
        if (sistema, arquitetura) == ("windows", "arm64"):
            teste_pty = '''import { spawn } from "bun-pty";
const pty = spawn("cmd.exe", ["/d", "/c", "echo PASSE1_PTY_OK"], { cols: 80, rows: 24, cwd: process.cwd(), env: process.env });
let saida = "";
const timer = setTimeout(() => { pty.kill(); process.exit(1); }, 15000);
pty.onData((s) => { saida += s; });
pty.onExit((e) => { clearTimeout(timer); if (e.exitCode !== 0 || !saida.includes("PASSE1_PTY_OK")) process.exit(1); });'''
            subprocess.run(["bun", "-e", teste_pty], cwd=fonte / "packages" / "core", check=True, timeout=30)
        subprocess.run(["bun", "test", "test/tool/shell.test.ts", "-t", "streams metadata updates progressively", "--timeout", "30000"], cwd=projeto, check=True, env=ambiente)
        subprocess.run(["bun", "test", "test/tool/read.test.ts", "test/tool/shell.test.ts", "test/tool/grep.test.ts", "test/tool/write.test.ts", "test/tool/edit.test.ts", "test/tool/apply_patch.test.ts", "--timeout", "30000"], cwd=projeto, check=True, env=ambiente)
        subprocess.run(["bun", "test", "test/session/prompt.test.ts", "-t", "sandbox protege shell direto", "--timeout", "30000"], cwd=projeto, check=True, env=ambiente)
        subprocess.run(["bun", "run", "typecheck"], cwd=projeto, check=True)
        subprocess.run(["bun", "run", "script/build.ts", "--single", "--skip-install", "--skip-embed-web-ui"], cwd=projeto, check=True, env=ambiente)
    finally:
        subprocess.run(["git", "apply", "--reverse", str(patch)], cwd=fonte, check=True)
        journal_temp.cleanup()
    nome = "opencode.exe" if os.name == "nt" else "opencode"
    origem = projeto / "dist" / f"opencode-{sistema}-{arquitetura}" / "bin" / nome
    if not origem.is_file():
        raise SystemExit(f"Binário esperado não encontrado: {origem}")
    destino = raiz / "passe1_engine" / "bin" / nome
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origem, destino)
    print(f"Motor compilado: {destino}")
