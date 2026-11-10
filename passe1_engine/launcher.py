"""Interface Passe1 usando motor OpenCode incorporado ao pacote."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
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
                opcoes["modalities"] = {"input": ["text", "image"], "output": ["text"]}
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
            try:
                info = arquivo.lstat()
                if arquivo.is_symlink() or not arquivo.is_file() or info.st_nlink != 1:
                    continue
            except OSError:
                continue
            return [str(arquivo)]
    return []



def _eh_windows():
    return os.name == "nt"


def _supervision(comando, ambiente):
    """Mantém o terminal e espera o motor terminar antes de limpar anexos."""
    anterior_int = signal.getsignal(signal.SIGINT)
    anterior_term = signal.getsignal(signal.SIGTERM)
    anterior_hup = signal.getsignal(signal.SIGHUP) if hasattr(signal, "SIGHUP") else None
    processo = None
    pendentes = []
    def encaminhar(signum, _quadro):
        if processo is None:
            pendentes.append(signum)
            return
        # Ctrl+C do terminal já chega ao grupo do filho. SIGTERM/SIGHUP
        # também podem ser enviados somente ao supervisor.
        if signum == signal.SIGINT:
            return
        try:
            processo.send_signal(signum)
        except ProcessLookupError:
            pass
    try:
        signal.signal(signal.SIGINT, encaminhar)
        signal.signal(signal.SIGTERM, encaminhar)
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, encaminhar)
        processo = subprocess.Popen(comando, env=ambiente)
        for signum in pendentes:
            try:
                processo.send_signal(signum)
            except ProcessLookupError:
                break
        codigo = processo.wait()
        return 128 - codigo if codigo < 0 else codigo
    except BaseException:
        if processo is not None and processo.poll() is None:
            processo.terminate()
            processo.wait()
        raise
    finally:
        signal.signal(signal.SIGINT, anterior_int)
        signal.signal(signal.SIGTERM, anterior_term)
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, anterior_hup)


def iniciar(argv=None, *, agente=None, tui=False):
    args = list(sys.argv[1:] if argv is None else argv)
    if agente is not None and agente.sandbox and (sys.platform != "darwin" or not os.access("/usr/bin/sandbox-exec", os.X_OK)):
        print("Sandbox de comandos disponível somente no macOS com sandbox-exec; nenhum pedido foi enviado.")
        return 1
    if args and args[0] in {"login", "status", "logout", "keys", "config", "models"}:
        return passe1.main(args)
    binario = Path(__file__).resolve().parent / "bin" / ("opencode.exe" if os.name == "nt" else "opencode")
    if not binario.is_file():
        print("Motor OpenCode não está instalado neste pacote. Instale o wheel da sua plataforma.")
        return 1
    base = passe1._base_url(agente.base_url if agente else None)
    chave = passe1._ler_chave(base)
    if not chave:
        print("Entre na conta Passe1 para continuar.")
        login = ["--base-url", base, "login"] if agente and agente.base_url else ["login"]
        if passe1.main(login) != 0:
            return 1
        chave = passe1._ler_chave(base)
    if not chave:
        print("Selecione uma chave no login antes de continuar.")
        return 1
    modelo = (agente.model if agente else None) or passe1._modelo_padrao()
    modelos = _catalogo(base, chave, modelo)
    config = {"provider": {"passe1": {"npm": "@ai-sdk/openai-compatible", "name": "Passe1", "options": {"baseURL": base}, "models": modelos}}, "model": "passe1/" + modelo, "enabled_providers": ["passe1"], "permission": {"edit": "ask", "bash": "ask", "external_directory": "ask", "webfetch": "ask", "websearch": "ask", "passe1_sensitive_read": "ask", "read": {"*": "allow", "*.env*": "ask", "*.pem": "ask", "*.key": "ask", "*.p12": "ask", "*.pfx": "ask", "*.keystore": "ask", "*.ssh/*": "ask", "*.aws/*": "ask", "*.gnupg/*": "ask", "*.config/*": "ask", "*.npmrc": "ask", "*.pypirc": "ask", "*.netrc": "ask", "*.dockerconfigjson": "ask", "*credentials*": "ask", "*secrets*": "ask", "*secret.*": "ask", "*token*": "ask", "*private_key*": "ask", "*id_rsa*": "ask", "*id_ed25519*": "ask", "*id_ecdsa*": "ask", "*service-account*": "ask"}}, "instructions": _instrucoes()}
    _guardar_credencial(chave)
    raiz = _raiz()
    ambiente = {nome: valor for nome, valor in os.environ.items() if not nome.startswith("OPENCODE_") and nome not in {"PASSE1_API_KEY", "PASSE1_SANDBOX_COMMANDS", "PASSE1_IMAGE_STAGE", "PASSE1_IMAGE_HASHES"}}
    ambiente["PWD"] = str(Path.cwd().resolve())
    if agente is not None and agente.sandbox:
        ambiente["PASSE1_SANDBOX_COMMANDS"] = "1"
    ambiente["PASSE1_BUNDLED_RG"] = str(Path(__file__).resolve().parent / "bin" / ("rg.exe" if os.name == "nt" else "rg"))
    ambiente["PASSE1_SECURE_PYTHON"] = sys.executable
    ambiente["PASSE1_SECURE_ROOT"] = str(Path(__file__).resolve().parent.parent)
    ambiente.update({"OPENCODE_CONFIG_CONTENT": json.dumps(config), "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1", "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1", "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1", "XDG_CONFIG_HOME": str(raiz / "config"), "XDG_DATA_HOME": str(raiz / "data"), "XDG_STATE_HOME": str(raiz / "state"), "XDG_CACHE_HOME": str(raiz / "cache")})
    if agente is not None:
        imagens_validadas = passe1._imagens(agente.image or [])
        comando = [str(binario)] + ([] if tui else ["run"]) + ["--pure", "-m", "passe1/" + modelo]
        if agente.session:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", agente.session):
                print("Nome de sessão inválido.")
                return 1
            identificador = hashlib.sha256((str(Path.cwd().resolve()) + "\0" + base + "\0" + modelo + "\0" + chave).encode()).hexdigest()[:16]
            titulo = "Passe1:" + agente.session + ":" + identificador
            try:
                listado = subprocess.run([str(binario), "session", "list", "--format", "json", "--pure"], env=ambiente, capture_output=True, text=True, timeout=30, check=True)
                sessoes = json.loads(listado.stdout) if listado.stdout.strip() else []
                if not isinstance(sessoes, list):
                    raise TypeError("lista inválida")
                diretorio = os.path.normcase(os.path.normpath(str(Path.cwd().resolve())))
                encontradas = [item for item in sessoes if isinstance(item, dict) and item.get("title") == titulo and isinstance(item.get("directory"), str) and os.path.normcase(os.path.normpath(item["directory"])) == diretorio]
                if len(encontradas) > 1:
                    raise ValueError("sessão ambígua")
                if encontradas:
                    id_sessao = encontradas[0].get("id")
                    if not isinstance(id_sessao, str) or not re.fullmatch(r"ses_[A-Za-z0-9_-]+", id_sessao):
                        raise ValueError("identificador inválido")
                    comando += ["--session", id_sessao]
                elif tui:
                    criada = subprocess.run([str(binario), "session", "create", "--title", titulo, "--pure"], env=ambiente, capture_output=True, text=True, timeout=30, check=True)
                    id_sessao = json.loads(criada.stdout).get("id")
                    if not isinstance(id_sessao, str) or not re.fullmatch(r"ses_[A-Za-z0-9_-]+", id_sessao):
                        raise ValueError("identificador inválido")
                    comando += ["--session", id_sessao]
                else:
                    comando += ["--title", titulo]
            except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
                print("Não foi possível localizar a sessão com segurança.")
                return 1
        comando.extend(agente.tarefa)
        pasta_imagens = None
        try:
            extensoes = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
            itens = []
            for parte in imagens_validadas:
                cabecalho, codificado = parte["image_url"]["url"].split(",", 1)
                mime = cabecalho.removeprefix("data:").removesuffix(";base64")
                itens.append((extensoes[mime], base64.b64decode(codificado, validate=True)))
            if not agente.tarefa and not tui:
                comando.append("--interactive")
            def executar_com_imagens(caminhos):
                if len(caminhos) != len(itens):
                    raise ValueError("Staging não retornou todas as imagens validadas.")
                hashes = {}
                for destino, (_, dados) in zip(caminhos, itens):
                    hashes[str(destino)] = hashlib.sha256(dados).hexdigest()
                    comando.extend(["--image" if tui else "--file", str(destino)])
                ambiente["PASSE1_IMAGE_HASHES"] = json.dumps(hashes)
                return _supervision(comando, ambiente)
            if itens and _eh_windows():
                from passe1_engine.win_image_adapter import preparar_imagens
                try:
                    with preparar_imagens(itens) as caminhos:
                        return executar_com_imagens(caminhos)
                except OSError as exc:
                    raise ValueError("Não foi possível preparar ou limpar o staging privado de imagens no Windows.") from exc
            if itens:
                pasta_imagens = Path(tempfile.mkdtemp(prefix="passe1-cli-images-"))
                caminhos = []
                for indice, (extensao, dados) in enumerate(itens):
                    destino = pasta_imagens / (f"imagem-{indice}" + extensao)
                    fd = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as arquivo:
                        arquivo.write(dados)
                    caminhos.append(destino)
                return executar_com_imagens(caminhos)
            return os.execvpe(str(binario), comando, ambiente)
        finally:
            if pasta_imagens is not None:
                try:
                    shutil.rmtree(pasta_imagens)
                except OSError as exc:
                    raise ValueError(f"Não foi possível remover o staging de imagens em {pasta_imagens}; remova-o manualmente.") from exc
    return os.execvpe(str(binario), [str(binario), "--pure", "-m", "passe1/" + modelo, *args], ambiente)


if __name__ == "__main__":
    raise SystemExit(iniciar())
