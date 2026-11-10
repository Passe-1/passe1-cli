"""Compila o OpenCode fixado para a plataforma local usando Bun 1.3.14."""
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

raiz = Path(__file__).resolve().parents[1]
fonte = raiz / "vendor" / "opencode"
if not (fonte / "package.json").is_file():
    raise SystemExit("Inicialize o submódulo: git submodule update --init --recursive")
if not shutil.which("bun"):
    raise SystemExit("Instale Bun 1.3.14 antes de compilar o motor.")
versao = subprocess.check_output(["bun", "--version"], text=True).strip()
if versao != "1.3.14":
    raise SystemExit(f"Bun 1.3.14 é necessário; instalado: {versao}.")
subprocess.run(["bun", "install", "--frozen-lockfile"], cwd=fonte, check=True)
match = re.search(r'^VERSAO = "([^"]+)"', (raiz / "passe1.py").read_text(encoding="utf-8"), re.MULTILINE)
if not match:
    raise SystemExit("Versão do Passe1 não encontrada.")
sistema = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(platform.system())
arquitetura = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(platform.machine().lower())
if not sistema or not arquitetura:
    raise SystemExit("Plataforma ainda não suportada pelo build.")
alvo_rg = f"{('macos' if sistema == 'darwin' else sistema)}-{arquitetura}"
bin_dir = raiz / "passe1_engine" / "bin"
subprocess.run([sys.executable, str(raiz / "scripts" / "fetch-rg.py"), "--target", alvo_rg, "--output", str(bin_dir)], check=True)
rg = bin_dir / ("rg.exe" if os.name == "nt" else "rg")
ambiente = os.environ.copy()
ambiente.update({"PASSE1_BUNDLED_RG": str(rg), "PASSE1_SECURE_PYTHON": sys.executable, "PASSE1_SECURE_ROOT": str(raiz), "OPENCODE_VERSION": match.group(1), "OPENCODE_CHANNEL": "passe1"})
projeto = fonte / "packages" / "opencode"
patch = raiz / "patches" / "opencode-hardlinks.patch"
subprocess.run(["git", "apply", "--check", str(patch)], cwd=fonte, check=True)
subprocess.run(["git", "apply", str(patch)], cwd=fonte, check=True)
try:
    subprocess.run(["bun", "test", "test/tool/read.test.ts", "test/tool/grep.test.ts", "test/tool/write.test.ts", "test/tool/edit.test.ts", "test/tool/apply_patch.test.ts", "--timeout", "30000"], cwd=projeto, check=True, env=ambiente)
    subprocess.run(["bun", "run", "typecheck"], cwd=projeto, check=True)
    subprocess.run(["bun", "run", "script/build.ts", "--single", "--skip-install", "--skip-embed-web-ui"], cwd=projeto, check=True, env=ambiente)
finally:
    subprocess.run(["git", "apply", "--reverse", str(patch)], cwd=fonte, check=True)
nome = "opencode.exe" if os.name == "nt" else "opencode"
origem = projeto / "dist" / f"opencode-{sistema}-{arquitetura}" / "bin" / nome
if not origem.is_file():
    raise SystemExit(f"Binário esperado não encontrado: {origem}")
destino = raiz / "passe1_engine" / "bin" / nome
destino.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(origem, destino)
print(f"Motor compilado: {destino}")
