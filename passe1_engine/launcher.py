"""Interface Passe1 usando motor OpenCode incorporado ao pacote."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

import httpx

import passe1


def _raiz():
    return Path(passe1._dir_config()) / "opencode"


def limpar_credencial():
    raiz = _raiz()
    for pasta in (raiz, raiz / "data", raiz / "data" / "opencode"):
        if pasta.is_symlink():
            raise OSError("Diretório de autenticação inválido.")
    (raiz / "data" / "opencode" / "auth.json").unlink(missing_ok=True)


def _guardar_credencial(chave):
    raiz = _raiz()
    diretorio = raiz / "data" / "opencode"
    for pasta in (raiz, raiz / "data", diretorio):
        if pasta.is_symlink():
            raise ValueError("Diretório de autenticação inválido.")
        pasta.mkdir(parents=True, mode=0o700, exist_ok=True)
        if os.name == "posix":
            os.chmod(pasta, 0o700)
    fd, nome = tempfile.mkstemp(prefix=".auth-", dir=diretorio)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
            json.dump({"passe1": {"type": "api", "key": chave}}, arquivo)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(nome, diretorio / "auth.json")
    finally:
        Path(nome).unlink(missing_ok=True)


def _catalogo(base, chave, padrao):
    modelos = {}
    try:
        with passe1._cliente(base, passe1.TIMEOUT_MODELOS) as cliente:
            resposta = cliente.get("/models", headers={"Authorization": f"Bearer {chave}"})
            resposta.raise_for_status()
            dados = resposta.json().get("data", [])
        for item in dados:
            if not isinstance(item, dict) or item.get("tipo") == "decisao":
                continue
            nome = item.get("id")
            if not isinstance(nome, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", nome):
                continue
            opcoes = {"name": nome}
            if item.get("tool_call") is True or nome == "glm-5.3-flash":
                opcoes["tool_call"] = True
            if item.get("vision") is True or nome == "gemini-3.1-flash-lite":
                opcoes["attachment"] = True
            modelos[nome] = opcoes
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        print("Não foi possível atualizar o catálogo; usando o modelo configurado.")
    if padrao not in modelos:
        modelos[padrao] = {"name": padrao}
        if padrao == "glm-5.3-flash":
            modelos[padrao]["tool_call"] = True
    return modelos


def _instrucoes():
    atual = Path.cwd().resolve()
    diretorios = []
    for diretorio in (atual, *atual.parents):
        diretorios.append(diretorio)
        if (diretorio / ".git").exists() or diretorio == Path.home():
            break
    for nome in ("AGENTS.md", "CLAUDE.md", "CONTEXT.md"):
        for diretorio in diretorios:
            arquivo = diretorio / nome
            if arquivo.is_file() and not arquivo.is_symlink():
                return [str(arquivo)]
    return []


def iniciar(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"login", "status", "logout", "keys", "config", "models"}:
        return passe1.main(args)
    binario = Path(__file__).resolve().parent / "bin" / ("opencode.exe" if os.name == "nt" else "opencode")
    if not binario.is_file():
        print("Motor OpenCode não está instalado neste pacote. Instale o wheel da sua plataforma.")
        return 1
    chave = passe1._ler_chave()
    if not chave:
        print("Entre na conta Passe1 para continuar.")
        if passe1.main(["login"]) != 0:
            return 1
        chave = passe1._ler_chave()
    if not chave:
        print("Selecione uma chave no login antes de continuar.")
        return 1
    modelo = passe1._modelo_padrao()
    base = passe1._base_url()
    modelos = _catalogo(base, chave, modelo)
    config = {"provider": {"passe1": {"npm": "@ai-sdk/openai-compatible", "name": "Passe1", "options": {"baseURL": base}, "models": modelos}}, "model": "passe1/" + modelo, "enabled_providers": ["passe1"], "permission": {"edit": "ask", "bash": "ask", "external_directory": "ask"}, "instructions": _instrucoes()}
    _guardar_credencial(chave)
    raiz = _raiz()
    ambiente = {nome: valor for nome, valor in os.environ.items() if not nome.startswith("OPENCODE_") and nome != "PASSE1_API_KEY"}
    ambiente.update({"OPENCODE_CONFIG_CONTENT": json.dumps(config), "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1", "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1", "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1", "XDG_CONFIG_HOME": str(raiz / "config"), "XDG_DATA_HOME": str(raiz / "data"), "XDG_STATE_HOME": str(raiz / "state"), "XDG_CACHE_HOME": str(raiz / "cache")})
    os.execvpe(str(binario), [str(binario), "--pure", "-m", "passe1/" + modelo, *args], ambiente)


if __name__ == "__main__":
    raise SystemExit(iniciar())
