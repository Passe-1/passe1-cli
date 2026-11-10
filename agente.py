"""Agente de programação com ferramentas locais e aprovação para alterações."""
from __future__ import annotations

import difflib
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import httpx

LIMITE = 30000
SISTEMA = ("Você é um agente de programação. Trabalhe no projeto atual usando as ferramentas. "
           "Leia os arquivos relevantes, faça alterações pontuais e execute testes. "
           "Não afirme que testou algo que não executou. Responda em português.")


def _ferramenta(nome, descricao, propriedades, obrigatorios):
    return {"type": "function", "function": {"name": nome, "description": descricao,
            "parameters": {"type": "object", "properties": propriedades,
                           "required": obrigatorios, "additionalProperties": False}}}


def _texto(descricao):
    return {"type": "string", "description": descricao}


FERRAMENTAS = [
    _ferramenta("listar", "Lista entradas de um diretório do projeto.", {"caminho": _texto("Diretório relativo")}, ["caminho"]),
    _ferramenta("ler_arquivo", "Lê arquivo de texto do projeto.", {"caminho": _texto("Caminho relativo")}, ["caminho"]),
    _ferramenta("buscar", "Busca texto literal em arquivos do projeto, ignorando diretórios ocultos e dependências.", {"texto": _texto("Texto a buscar")}, ["texto"]),
    _ferramenta("editar_arquivo", "Substitui exatamente uma ocorrência de texto em arquivo existente.", {"caminho": _texto("Caminho relativo"), "antigo": _texto("Texto original não vazio"), "novo": _texto("Texto substituto")}, ["caminho", "antigo", "novo"]),
    _ferramenta("criar_arquivo", "Cria arquivo novo no projeto.", {"caminho": _texto("Caminho relativo"), "conteudo": _texto("Conteúdo completo")}, ["caminho", "conteudo"]),
    _ferramenta("terminal", "Executa comando no diretório do projeto após aprovação.", {"comando": _texto("Comando de shell")}, ["comando"]),
]


def _aprovar(pergunta, ler):
    try:
        return ler(pergunta).strip().lower() in {"s", "sim"}
    except (EOFError, KeyboardInterrupt):
        return False


def _arquivo_sensivel(caminho: Path) -> bool:
    partes = [parte.lower() for parte in caminho.parts]
    nome = caminho.name.lower()
    return (any(p in {".ssh", ".aws", ".gnupg", ".config"} for p in partes)
            or nome == ".env"
            or nome in {".npmrc", ".pypirc", ".netrc", ".dockerconfigjson", ".envrc"}
            or nome.startswith((".env.", "credentials.", "secrets.", "secret.", "token.", "private_key", "id_rsa", "id_ed25519", "id_ecdsa", "service-account"))
            or nome in {"credentials", "credentials.json", "token", "token.json", "secrets", "secrets.json", "id_rsa", "id_ed25519"}
            or nome.endswith((".env", ".pem", ".key", ".p12", ".pfx", ".keystore")))


def executar_ferramenta(raiz: Path, nome: str, args: dict, ler=input) -> str:
    try:
        if not isinstance(args, dict):
            return "Erro: argumentos devem ser um objeto."
        if nome == "buscar":
            busca = args["texto"]
            if not isinstance(busca, str) or not busca or len(busca) > 500:
                return "Erro: texto de busca inválido."
            resultados = []
            for pasta, diretorios, arquivos in os.walk(raiz, followlinks=False):
                diretorios[:] = sorted(d for d in diretorios if not d.startswith(".") and d not in {"node_modules", "__pycache__", "venv", "build", "dist"})
                for nome_arquivo in sorted(arquivos):
                    if nome_arquivo.startswith("."):
                        continue
                    arquivo = Path(pasta) / nome_arquivo
                    try:
                        if arquivo.is_symlink() or _arquivo_sensivel(arquivo.relative_to(raiz)) or arquivo.stat().st_size > 1_000_000:
                            continue
                        with arquivo.open(encoding="utf-8") as conteudo:
                            for numero, linha in enumerate(conteudo, 1):
                                if busca in linha:
                                    resultados.append(f"{arquivo.relative_to(raiz)}:{numero}:{linha.rstrip()[:500]}")
                                    if len(resultados) >= 100:
                                        return "\n".join(resultados)[:LIMITE] + "\n[busca limitada]"
                    except (OSError, UnicodeError):
                        continue
            return "\n".join(resultados)[:LIMITE] or "Nenhuma ocorrência encontrada."
        if nome == "terminal":
            comando = args["comando"]
            if not isinstance(comando, str) or not comando.strip():
                return "Erro: comando inválido."
            print(f"Comando proposto (pode alterar arquivos e acessar a rede):\n{comando}")
            if not _aprovar("Executar? [s/N] ", ler):
                return "Execução recusada pelo usuário."
            with tempfile.TemporaryFile() as saida:
                try:
                    resultado = subprocess.run(comando, shell=True, check=False, cwd=raiz, stdout=saida,
                                               stderr=subprocess.STDOUT, timeout=120)
                except subprocess.TimeoutExpired:
                    return "Comando interrompido após 120 segundos."
                saida.seek(0)
                texto = saida.read(LIMITE + 1).decode("utf-8", errors="replace")
            return f"Código de saída: {resultado.returncode}\n{texto[:LIMITE]}" + ("\n[saída truncada]" if len(texto) > LIMITE else "")
        if nome not in {"listar", "ler_arquivo", "editar_arquivo", "criar_arquivo"}:
            return "Erro: ferramenta desconhecida."
        caminho_solicitado = raiz / args["caminho"]
        caminho = caminho_solicitado.resolve()
        if not caminho.is_relative_to(raiz.resolve()):
            return "Erro: caminho fora do projeto."
        if nome == "listar":
            entradas = sorted(p.name + ("/" if p.is_dir() else "") for p in caminho.iterdir())
            return "\n".join(entradas)[:LIMITE]
        if (nome in {"ler_arquivo", "editar_arquivo"}
                and (_arquivo_sensivel(caminho.relative_to(raiz.resolve())) or _arquivo_sensivel(caminho_solicitado.relative_to(raiz)))
                and not _aprovar(f"Ler arquivo sensível {caminho.relative_to(raiz.resolve())} e enviar ao modelo? [s/N] ", ler)):
            return "Leitura de arquivo sensível recusada pelo usuário."
        if nome == "ler_arquivo":
            with caminho.open(encoding="utf-8") as arquivo:
                texto = arquivo.read(LIMITE + 1)
            return texto[:LIMITE] + ("\n[arquivo truncado]" if len(texto) > LIMITE else "")
        if nome == "criar_arquivo":
            if caminho.exists():
                return "Erro: arquivo já existe."
            anterior, novo = "", args["conteudo"]
        else:
            anterior = caminho.read_text(encoding="utf-8")
            antigo, substituto = args["antigo"], args["novo"]
            if not antigo or anterior.count(antigo) != 1:
                return "Erro: texto original deve ocorrer exatamente uma vez."
            novo = anterior.replace(antigo, substituto, 1)
        diff = "".join(difflib.unified_diff(anterior.splitlines(keepends=True), novo.splitlines(keepends=True), fromfile=str(caminho), tofile=str(caminho)))
        print(f"Alteração proposta:\n{diff}")
        if not _aprovar("Aplicar alteração? [s/N] ", ler):
            return "Alteração recusada pelo usuário."
        if nome == "criar_arquivo":
            caminho.parent.mkdir(parents=True, exist_ok=True)
            with caminho.open("x", encoding="utf-8") as arquivo:
                arquivo.write(novo)
        else:
            if caminho.read_text(encoding="utf-8") != anterior:
                return "Erro: arquivo mudou durante a aprovação."
            caminho.write_text(novo, encoding="utf-8")
        return "Alteração aplicada."
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        return f"Erro na ferramenta: {exc}"


def executar(base, chave, modelo, tarefa, cliente, ler=input, interativo=False, imagens=None):
    raiz = Path.cwd().resolve()
    sistema = SISTEMA
    instrucoes = raiz / "AGENTS.md"
    try:
        if instrucoes.is_file() and instrucoes.resolve().is_relative_to(raiz):
            with instrucoes.open(encoding="utf-8") as arquivo:
                sistema += "\n\nInstruções do projeto (AGENTS.md):\n" + arquivo.read(LIMITE)
    except (OSError, UnicodeError):
        print("Não foi possível ler AGENTS.md.")
    mensagens = [{"role": "system", "content": sistema}]
    conversa_id = uuid.uuid4().hex
    if tarefa:
        conteudo = [{"type": "text", "text": tarefa}, *imagens] if imagens else tarefa
        mensagens.append({"role": "user", "content": conteudo})
        imagens = None
    print(f"Projeto: {raiz}\nModelo: {modelo}")
    try:
        with cliente(base, 120.0) as http:
            rodadas = 0
            while True:
                if len(mensagens) == 1 or mensagens[-1]["role"] == "assistant" and not mensagens[-1].get("tool_calls"):
                    if not interativo:
                        return True, ""
                    try:
                        tarefa = ler("passe1> ").strip()
                    except (EOFError, KeyboardInterrupt):
                        return True, ""
                    if tarefa in {"/sair", "/exit", "/quit"}:
                        return True, ""
                    if tarefa == "/limpar":
                        mensagens = mensagens[:1]
                        conversa_id = uuid.uuid4().hex
                        print("Contexto limpo.")
                        continue
                    if not tarefa:
                        continue
                    conteudo = [{"type": "text", "text": tarefa}, *imagens] if imagens else tarefa
                    mensagens.append({"role": "user", "content": conteudo})
                    imagens = None
                    rodadas = 0
                if rodadas >= 40:
                    return False, "Limite de 40 rodadas atingido."
                rodadas += 1
                resposta = http.post("/chat/completions", headers={"Authorization": f"Bearer {chave}", "X-Conversation-ID": conversa_id},
                                     json={"model": modelo, "messages": mensagens, "tools": FERRAMENTAS, "stream": False})
                if resposta.status_code != 200:
                    return False, f"Gateway respondeu HTTP {resposta.status_code}: {resposta.text[:1000]}"
                corpo = resposta.json()
                mensagem = corpo["choices"][0]["message"]
                uso = corpo.get("usage") or {}
                detalhes = uso.get("prompt_tokens_details") or {}
                if isinstance(detalhes.get("cached_tokens"), int):
                    print(f"Cache: {detalhes['cached_tokens']} tokens de entrada reutilizados.")
                chamadas = mensagem.get("tool_calls") or []
                if mensagem.get("content"):
                    print(mensagem["content"])
                if not chamadas:
                    if not mensagem.get("content"):
                        return False, "O modelo não retornou uma resposta nem chamadas de ferramentas."
                    mensagens.append(mensagem)
                    continue
                mensagens.append(mensagem)
                for chamada in chamadas:
                    funcao = chamada["function"]
                    try:
                        argumentos = json.loads(funcao["arguments"])
                    except (ValueError, TypeError):
                        resultado = "Erro: argumentos JSON inválidos."
                    else:
                        print(f"Ferramenta: {funcao['name']}")
                        resultado = executar_ferramenta(raiz, funcao["name"], argumentos, ler)
                    mensagens.append({"role": "tool", "tool_call_id": chamada["id"], "content": resultado})
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        return False, f"Falha ao conversar com o gateway: {type(exc).__name__}."
