"""Instala o wheel do Passe1 compatível com a plataforma, a partir da release pública."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

API = "https://api.github.com/repos/Passe-1/passe1-cli/releases?per_page=20"
PREFIXO = "/Passe-1/passe1-cli/releases/download/"


def selecionar(release, tags):
    try:
        from pip._vendor.packaging.utils import parse_wheel_filename
        from pip._vendor.packaging.version import Version
    except ImportError as exc:
        raise RuntimeError("Python precisa de pip para identificar o wheel compatível.") from exc
    versao = Version(str(release.get("tag_name", "")).removeprefix("v"))
    ordem = {tag: indice for indice, tag in enumerate(tags)}
    candidatos = []
    for asset in release.get("assets", []):
        nome, url = asset.get("name", ""), asset.get("browser_download_url", "")
        try:
            pacote, v, _, wheel_tags = parse_wheel_filename(nome)
            destino = urlsplit(url)
            if pacote != "passe1" or v != versao or destino.scheme != "https" or destino.netloc != "github.com" or not destino.path.startswith(PREFIXO) or not destino.path.endswith("/" + nome):
                continue
            indice = min((ordem[t] for t in wheel_tags if t in ordem), default=None)
            if indice is not None:
                candidatos.append((indice, url))
        except (TypeError, ValueError):
            continue
    if not candidatos:
        raise RuntimeError("Não há wheel compatível com este Python e sistema nesta release.")
    return min(candidatos)[1]


def escolher_release(releases, tags):
    tags = list(tags)
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        try:
            return release.get("tag_name"), selecionar(release, tags)
        except (RuntimeError, ValueError):
            continue
    raise RuntimeError("Nenhuma release publicada contém wheel compatível com este sistema.")


def main():
    try:
        url = os.environ.get("PASSE1_SPEC")
        if url:
            print("Instalando a especificação fornecida em PASSE1_SPEC.")
        else:
            from pip._vendor.packaging.tags import sys_tags
            req = Request(API, headers={"User-Agent": "passe1-installer", "Accept": "application/vnd.github+json"})
            with urlopen(req, timeout=30) as resposta:
                releases = json.load(resposta)
            if not isinstance(releases, list):
                raise ValueError("A lista de releases recebida é inválida.")
            tag, url = escolher_release(releases, sys_tags())
            print(f"Instalando a release compatível {tag}.")
        pipx = shutil.which("pipx")
        comando = [pipx, "install", "--force", url] if pipx else [sys.executable, "-m", "pip", "install", "--user", "--upgrade", url]
        subprocess.run(comando, check=True)
        print("Passe1 instalado. Execute: passe1 login")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Não foi possível instalar: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
