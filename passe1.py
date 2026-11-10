#!/usr/bin/env python3
"""CLI do Passe1: login, chat no terminal, setup de harnesses e status.

Fatia 1: o essencial para o cliente usar o passe fora do navegador.
Fatia 2: models, status rico (/v1/me), chat com streaming, historico
do terminal e skills locais (SKILL.md).
Toda conversa com o gateway usa o formato OpenAI em ``/v1``.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import getpass
import hashlib
import json
import os
import re
import shutil
import sys
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from terminal import seguro as _terminal_seguro

VERSAO = "0.3.0"
SISTEMA_PADRAO = "Você é o assistente do Passe1. Responda SEMPRE em português do Brasil, de forma clara e direta. Nunca misture idiomas na mesma resposta. Só use outro idioma se a pessoa pedir explicitamente."
BASE_URL_PADRAO = "https://passe.dehor.com.br/v1"
PREFIXO_CHAVE = "sk-passe1-"
MODELO_PADRAO = "glm-5.3-flash"
TIMEOUT_MODELOS = 15.0
TIMEOUT_CHAT = 60.0

# Transporte injetavel: os testes trocam por httpx.MockTransport para
# nunca tocar a rede. Em uso normal fica None (rede de verdade).
_transporte = None


def _hoje() -> Path:
    """Home do usuario; PASSE1_HOME existe so para os testes isolarem."""
    return Path(os.environ.get("PASSE1_HOME") or Path.home())


def _dir_config() -> Path:
    return Path(os.environ.get("PASSE1_CONFIG_DIR") or _hoje() / ".config" / "passe1")


def _arquivo_chave() -> Path:
    return _dir_config() / "key"


def _base_url(args_base: str | None = None) -> str:
    base = (args_base or os.environ.get("PASSE1_BASE_URL") or BASE_URL_PADRAO).rstrip("/")
    _origem(base)
    return base


def _cliente(base_url: str, timeout: float) -> httpx.Client:
    _origem(base_url)
    return httpx.Client(base_url=base_url, timeout=timeout, transport=_transporte)


def _ler_chave(base: str | None = None) -> str | None:
    sessao = _ler_sessao()
    if not sessao and (_dir_config() / "key-session").exists():
        return None
    if sessao:
        try:
            if _origem(base or _base_url()) != sessao["origem"]:
                return None
        except ValueError:
            return None
        try:
            binding = (_dir_config() / "key-session").read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if binding != hashlib.sha256(sessao["token"].encode()).hexdigest():
            return None
    try:
        texto = _arquivo_chave().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return texto or None


def _guardar_chave(chave: str) -> None:
    destino = _arquivo_chave()
    destino.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(destino.parent, 0o700)
    fd = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (chave.strip() + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(destino, 0o600)
    sessao = _ler_sessao()
    if sessao:
        (_dir_config() / "key-session").write_text(
            hashlib.sha256(sessao["token"].encode()).hexdigest(), encoding="utf-8"
        )


def _origem(base: str) -> str:
    url = urlsplit(base)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise ValueError("URL do gateway inválida.")
    if url.scheme != "https" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("O gateway exige HTTPS (HTTP permitido apenas no localhost).")
    return f"{url.scheme}://{url.netloc}"


def _guardar_sessao(origem: str, nome: str, token: str) -> None:
    destino = _dir_config() / "session"
    destino.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(destino.parent, 0o700)
    fd = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
        json.dump({"origem": origem, "nome": nome, "token": token}, arquivo)
    os.chmod(destino, 0o600)


def _ler_sessao() -> dict | None:
    try:
        dados = json.loads((_dir_config() / "session").read_text(encoding="utf-8"))
        if not isinstance(dados, dict) or not all(isinstance(dados.get(k), str) and dados[k] for k in ("origem", "nome", "token")):
            return None
        if dados["nome"] not in ("passe1_session", "__Host-passe1_session"):
            return None
        return dados
    except (OSError, ValueError):
        return None


def _cliente_conta(base: str, sessao: dict) -> httpx.Client:
    origem = _origem(base)
    if origem != sessao["origem"]:
        raise ValueError("A sessão pertence a outro gateway; faça login novamente.")
    return httpx.Client(base_url=origem, timeout=TIMEOUT_MODELOS, transport=_transporte,
                        cookies={sessao["nome"]: sessao["token"]})


def _reais(centavos) -> str:
    valor = int(centavos)
    return f"{'-' if valor < 0 else ''}R$ {abs(valor) // 100},{abs(valor) % 100:02d}"


def _modelo_padrao() -> str:
    try:
        configurado = (_dir_config() / "model").read_text(encoding="utf-8").strip()
    except OSError:
        configurado = ""
    return os.environ.get("PASSE1_MODEL") or configurado or MODELO_PADRAO


def cmd_config(args: argparse.Namespace) -> int:
    if args.valor is None:
        print(_modelo_padrao())
        return 0
    valor = args.valor.strip()
    if not valor or "\n" in valor:
        print("Informe um identificador de modelo válido.")
        return 1
    _dir_config().mkdir(parents=True, exist_ok=True)
    (_dir_config() / "model").write_text(valor + "\n", encoding="utf-8")
    print(f"Modelo padrão salvo: {valor}.")
    return 0


def _login_conta(args: argparse.Namespace) -> int:
    try:
        origem = _origem(_base_url(args.base_url))
        url = urlsplit(origem)
        if url.scheme != "https" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            print("Login de conta exige HTTPS (HTTP permitido apenas no localhost).")
            return 1
        with httpx.Client(base_url=origem, timeout=TIMEOUT_MODELOS, transport=_transporte) as cliente:
            inicio = cliente.post("/api/cli/iniciar")
            if inicio.status_code != 200:
                print(f"Não consegui iniciar o login (HTTP {inicio.status_code}).")
                return 1
            dados_inicio = inicio.json()
            browser_code = dados_inicio["browser_code"]
            device_code = dados_inicio["device_code"]
            if not all(isinstance(c, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", c) for c in (browser_code, device_code)):
                print("O gateway retornou uma autorização inválida.")
                return 1
            url_autorizacao = f"{origem}/cli/autorizar?code={browser_code}"
            print(f"Código de confirmação: {browser_code[:8]}\nConfira este código na tela. Não autorize se for diferente.\nAbra o navegador para autorizar o Passe1 CLI:\n{url_autorizacao}")
            try:
                webbrowser.open(url_autorizacao)
            except (OSError, webbrowser.Error):
                print("Abra o endereço acima manualmente.")
            limite = time.monotonic() + min(int(dados_inicio.get("expires_in", 600)), 600)
            while time.monotonic() < limite:
                resposta = cliente.post("/api/cli/token", json={"device_code": device_code})
                if resposta.status_code == 200:
                    break
                if resposta.status_code != 202:
                    print("Autorização recusada ou expirada. Execute passe1 login novamente.")
                    return 1
                time.sleep(2)
            else:
                print("Autorização expirou. Execute passe1 login novamente.")
                return 1
            nome = next((n for n in ("__Host-passe1_session", "passe1_session") if cliente.cookies.get(n)), None)
            if not nome:
                print("O gateway não retornou uma sessão válida.")
                return 1
            sessao = {"origem": origem, "nome": nome, "token": cliente.cookies.get(nome)}
            me = cliente.get("/api/me")
            if me.status_code != 200:
                print(f"Não consegui consultar a conta (HTTP {me.status_code}).")
                return 1
            dados = me.json()
            credito = dados.get("credito") or {}
            _guardar_sessao(origem, nome, sessao["token"])
            print(_terminal_seguro(f"Conta: {dados.get('usuario', {}).get('email', '')}"))
            print(f"Saldo: {_reais(credito.get('saldo_cents', 0))} | bônus: {_reais(credito.get('bonus_cents', 0))}")
            ativas = [k for k in dados.get("chaves", []) if k.get("status") == "ativa"]
            if not ativas:
                print("Nenhuma chave ativa. Recarregue se necessário e rode: passe1 keys create")
                return 0
            print("Escolha uma chave para usar neste computador (0 para continuar sem chave):")
            for i, chave in enumerate(ativas, 1):
                print(_terminal_seguro(f"  {i}. {chave.get('masked', 'chave')} {chave.get('label', '')}"))
            try:
                escolha = int(input("Chave: ").strip())
            except (ValueError, EOFError):
                escolha = 0
            if escolha == 0:
                return 0
            if escolha < 1 or escolha > len(ativas):
                print("Opção inválida. Entre novamente para selecionar uma chave.")
                return 1
            revelar = cliente.post("/api/keys/reveal", json={"id": ativas[escolha - 1]["id"]})
            if revelar.status_code != 200:
                _, detalhe = _codigo_erro(revelar)
                print(_terminal_seguro(detalhe or f"Não consegui recuperar a chave (HTTP {revelar.status_code})."))
                return 1
            chave = revelar.json().get("key", "")
            if not isinstance(chave, str) or not chave.startswith(PREFIXO_CHAVE):
                print("O gateway retornou uma chave inválida.")
                return 1
            _guardar_chave(chave)
            print("Chave selecionada e guardada neste computador.")
            return 0
    except ValueError as exc:
        print(str(exc))
        return 1
    except (httpx.HTTPError, OSError, EOFError, KeyError, TypeError) as exc:
        print(f"Não consegui entrar na conta ({type(exc).__name__}).")
        return 1


def cmd_keys(args: argparse.Namespace) -> int:
    sessao = _ler_sessao()
    if not sessao:
        print("Entre na conta primeiro: passe1 login")
        return 1


    try:
        with _cliente_conta(_base_url(args.base_url), sessao) as cliente:
            if args.acao == "create":
                resposta = cliente.post("/api/keys")
                if resposta.status_code != 201:
                    _, detalhe = _codigo_erro(resposta)
                    print(_terminal_seguro(detalhe or f"Não consegui criar a chave (HTTP {resposta.status_code})."))
                    return 1
                chave = resposta.json().get("key", "")
                if not isinstance(chave, str) or not chave.startswith(PREFIXO_CHAVE):
                    print("O gateway retornou uma chave inválida; confira as chaves no painel.")
                    return 1
                _guardar_chave(chave)
                print("Chave criada e selecionada neste computador.")
                return 0
            resposta = cliente.get("/api/me")
            if resposta.status_code != 200:
                print(f"Não consegui consultar as chaves (HTTP {resposta.status_code}).")
                return 1
            chaves = resposta.json().get("chaves", [])
            if args.json:
                print(json.dumps(chaves, ensure_ascii=False))
            elif not chaves:
                print("Nenhuma chave. Para criar: passe1 keys create")
            else:
                for chave in chaves:
                    print(_terminal_seguro(f"{chave.get('masked', '-')}  {chave.get('status', '-')}  {chave.get('label', '')}"))
            return 0
    except (httpx.HTTPError, ValueError, OSError, TypeError) as exc:
        print(f"Não consegui acessar as chaves ({type(exc).__name__}).")
        return 1


def cmd_usage(args: argparse.Namespace) -> int:
    sessao = _ler_sessao()
    if not sessao:
        print("Entre na conta primeiro: passe1 login")
        return 1
    try:
        with _cliente_conta(_base_url(args.base_url), sessao) as cliente:
            resposta = cliente.get("/api/me/uso", params={"dias": args.dias})
        if resposta.status_code != 200:
            print(f"Não consegui consultar o uso (HTTP {resposta.status_code}).")
            return 1
        dados = resposta.json()
        if args.json:
            print(json.dumps(dados, ensure_ascii=False))
            return 0
        modelos = dados.get("por_modelo", [])
        if not modelos:
            print("Nenhum uso no período.")
        for item in modelos:
            custo = item.get("custo_centavos")
            valor = _reais(custo) if custo is not None else "custo indisponível"
            print(_terminal_seguro(f"{item['modelo']}: {item['requests']} chamadas, {item['tokens_entrada']} tokens de entrada, {item['tokens_saida']} de saída, {valor}."))
        return 0
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        print(f"Não consegui consultar o uso ({type(exc).__name__}).")
        return 1

def _codigo_erro(resp: httpx.Response) -> tuple[str, str]:
    """Extrai (code, message) do envelope de erro do gateway, com fallback."""
    try:
        erro = resp.json().get("error", {})
    except ValueError:
        return "", f"o gateway respondeu HTTP {resp.status_code} sem JSON."
    if not isinstance(erro, dict):
        return "", f"o gateway respondeu HTTP {resp.status_code}."
    return str(erro.get("code", "")), str(erro.get("message", ""))


def _msg_401(codigo: str, detalhe: str) -> str:
    if codigo == "pass_expired":
        return "Passe expirado. Renove em https://passe.dehor.com.br/comprar."
    if codigo == "api_key_revoked":
        return "Essa chave foi revogada. Gere outra no painel e faca login de novo."
    if codigo == "invalid_api_key":
        return "Chave desconhecida. Confira se voce copiou a chave inteira."
    return detalhe or "Chave rejeitada pelo gateway (HTTP 401)."


def _sem_login() -> int:
    print("Nenhuma chave disponível para este gateway. Entre na conta com: passe1 login")
    return 1


def _plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um if n == 1 else varios}"


def cmd_login(args: argparse.Namespace) -> int:
    if not args.chave and not args.key:
        return _login_conta(args)
    chave = args.chave or getpass.getpass("Cole sua chave (sk-passe1-...): ")
    chave = chave.strip()
    if not chave.startswith(PREFIXO_CHAVE):
        print(f"Formato invalido: a chave precisa comecar com '{PREFIXO_CHAVE}'.")
        return 1
    base = _base_url(args.base_url)
    try:
        resp = _cliente(base, TIMEOUT_MODELOS).get(
            "/models", headers={"Authorization": f"Bearer {chave}"}
        )
    except httpx.HTTPError as exc:
        print(f"Nao consegui falar com o gateway ({exc.__class__.__name__}).")
        print("Confira sua conexao e tente de novo.")
        return 1
    if resp.status_code == 200:
        for nome in ("session", "key-session"):
            (_dir_config() / nome).unlink(missing_ok=True)
        _guardar_chave(chave)
        print("Login ok! Passe valido e chave guardada com seguranca.")
        return 0
    if resp.status_code == 401:
        codigo, detalhe = _codigo_erro(resp)
        print(_terminal_seguro(_msg_401(codigo, detalhe)))
        return 1
    codigo, detalhe = _codigo_erro(resp)
    print(_terminal_seguro(detalhe or f"O gateway respondeu HTTP {resp.status_code}."))
    return 1


def cmd_logout(args: argparse.Namespace) -> int:
    from passe1_engine.launcher import limpar_credencial
    erro_motor = False
    try:
        limpar_credencial()
    except OSError:
        print("Não foi possível apagar a credencial do motor OpenCode.")
        erro_motor = True
    sessao = _ler_sessao()
    if sessao:
        try:
            with _cliente_conta(_base_url(args.base_url), sessao) as cliente:
                resposta = cliente.post("/api/contas/logout")
                if resposta.status_code != 200:
                    print(f"Não consegui encerrar a sessão no servidor (HTTP {resposta.status_code}).")
        except (httpx.HTTPError, ValueError):
            print("Não consegui encerrar a sessão no servidor; removendo dados locais.")
        for nome in ("session", "key-session"):
            (_dir_config() / nome).unlink(missing_ok=True)
    try:
        _arquivo_chave().unlink()
    except FileNotFoundError:
        print("Voce ja estava deslogado.")
        return 1 if erro_motor else 0
    except OSError as exc:
        print(f"Nao consegui apagar a chave guardada: {exc}.")
        return 1
    print("Chave do CLI apagada deste computador." if erro_motor else "Logout ok! Chave apagada deste computador.")
    return 1 if erro_motor else 0


def cmd_models(args: argparse.Namespace) -> int:
    chave = _ler_chave(_base_url(args.base_url))
    if chave is None:
        return _sem_login()
    base = _base_url(args.base_url)
    try:
        resp = _cliente(base, TIMEOUT_MODELOS).get(
            "/models", headers={"Authorization": f"Bearer {chave}"}
        )
    except httpx.HTTPError as exc:
        print(f"Nao consegui falar com o gateway ({exc.__class__.__name__}).")
        return 1
    if resp.status_code == 401:
        codigo, detalhe = _codigo_erro(resp)
        print(_terminal_seguro(_msg_401(codigo, detalhe)))
        return 1
    if resp.status_code != 200:
        _, detalhe = _codigo_erro(resp)
        print(_terminal_seguro(detalhe or f"O gateway respondeu HTTP {resp.status_code}."))
        return 1
    try:
        dados = resp.json().get("data", [])
    except ValueError:
        dados = []
    if not isinstance(dados, list):
        dados = []
    if args.json:
        print(json.dumps(dados, ensure_ascii=False))
        return 0
    print(f"Modelos disponiveis: {len(dados)}")
    for modelo in dados:
        if not isinstance(modelo, dict) or "id" not in modelo:
            continue
        partes = [f"  - {modelo['id']}"]
        if modelo.get("vision") is True:
            partes.append("[visao]")
        janela = modelo.get("context_window")
        if janela:
            partes.append(f"(janela: {janela} tokens)")
        print(_terminal_seguro(" ".join(partes)))
    return 0


def _linha_uso(rotulo: str, bloco) -> None:
    try:
        req = int(bloco["requisicoes"])
        tok = int(bloco["tokens"])
        err = int(bloco["erros"])
    except (KeyError, TypeError, ValueError):
        print(f"{rotulo}: dado indisponivel.")
        return
    print(
        f"{rotulo}: {_plural(req, 'requisicao', 'requisicoes')}, "
        f"{_plural(tok, 'token', 'tokens')}, {_plural(err, 'erro', 'erros')}."
    )


def cmd_status(args: argparse.Namespace) -> int:
    sessao = _ler_sessao()
    if sessao:
        try:
            with _cliente_conta(_base_url(args.base_url), sessao) as cliente:
                resp = cliente.get("/api/me/saldo")
            if resp.status_code == 401:
                print("Sessão expirada. Rode: passe1 login")
                return 1
            if resp.status_code != 200:
                print(f"Não consegui consultar o saldo (HTTP {resp.status_code}).")
                return 1
            saldo = resp.json()
            if args.json:
                print(json.dumps(saldo, ensure_ascii=False))
            else:
                print(f"Saldo: {_reais(saldo['saldo_centavos'])}")
                print(f"Bônus: {_reais(saldo.get('bonus_centavos', 0))}")
            return 0
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            print(f"Não consegui consultar o saldo: {type(exc).__name__}.")
            return 1
    chave = _ler_chave(_base_url(args.base_url))
    if chave is None:
        return _sem_login()
    base = _base_url(args.base_url)
    try:
        resp = _cliente(base, TIMEOUT_MODELOS).get(
            "/me", headers={"Authorization": f"Bearer {chave}"}
        )
    except httpx.HTTPError as exc:
        print(f"Nao consegui falar com o gateway ({exc.__class__.__name__}).")
        return 1
    if resp.status_code == 401:
        codigo, detalhe = _codigo_erro(resp)
        print(_terminal_seguro(_msg_401(codigo, detalhe)))
        return 1
    if resp.status_code != 200:
        _, detalhe = _codigo_erro(resp)
        print(_terminal_seguro(detalhe or f"O gateway respondeu HTTP {resp.status_code}."))
        return 1
    try:
        corpo = resp.json()
    except ValueError:
        corpo = None
    if args.json:
        if not isinstance(corpo, dict):
            print("O gateway respondeu num formato que nao entendi.")
            return 1
        print(json.dumps(corpo, ensure_ascii=False))
        return 0
    try:
        passe = corpo["passe"]
        uso = corpo["uso"]
        dia = uso["ultimas_24h"]
        semana = uso["ultimos_7d"]
    except (KeyError, TypeError):
        print("O gateway respondeu num formato que nao entendi.")
        return 1
    print("Passe valido." if passe.get("ativo", True) else "Passe inativo.")
    print(f"Plano: {passe.get('tier') or '-'}.")
    dias = passe.get("dias_restantes")
    expira = passe.get("expira_em")
    data = ""
    if expira:
        try:
            quando = datetime.datetime.fromtimestamp(float(expira))
            data = f" (ate {quando.strftime('%d/%m/%Y')})"
        except (ValueError, TypeError, OSError, OverflowError):
            data = ""
    if dias is not None:
        try:
            print(f"Valido por mais {_plural(int(dias), 'dia', 'dias')}{data}.")
        except (TypeError, ValueError):
            print("Sem data de expiracao.")
    elif data:
        print(f"Expira{data}.")
    else:
        print("Sem data de expiracao.")
    _linha_uso("Uso nas ultimas 24h", dia)
    _linha_uso("Uso nos ultimos 7 dias", semana)
    return 0


def _post_chat(base: str, chave: str, modelo: str, mensagens: list) -> httpx.Response:
    cliente = _cliente(base, TIMEOUT_CHAT)
    try:
        pedido = cliente.build_request("POST", "/chat/completions", headers={"Authorization": f"Bearer {chave}"}, json={"model": modelo, "messages": mensagens, "stream": True})
        resposta = cliente.send(pedido, stream=True)
    except BaseException:
        cliente.close()
        raise
    resposta.extensions["passe1_cliente"] = cliente
    return resposta


def _texto_resposta(resp: httpx.Response) -> tuple[str | None, str | None]:
    """Retorna (texto, erro): um dos dois sempre vem preenchido."""
    if resp.status_code == 401:
        codigo, detalhe = _codigo_erro(resp)
        return None, _msg_401(codigo, detalhe)
    if resp.status_code == 429:
        _, detalhe = _codigo_erro(resp)
        return None, detalhe or "Limite atingido. Aguarde um pouco e tente de novo."
    if resp.status_code != 200:
        _, detalhe = _codigo_erro(resp)
        return None, detalhe or f"O gateway respondeu HTTP {resp.status_code}."
    try:
        corpo = resp.json()
        escolha = corpo["choices"][0]
        motivo = escolha.get("finish_reason")
        if motivo == "length":
            return None, "Resposta incompleta: limite de tokens atingido."
        if motivo not in (None, "stop"):
            return None, "Resposta interrompida pelo modelo."
        texto = escolha["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return None, "O gateway respondeu num formato que nao entendi."
    if not isinstance(texto, str):
        return None, "O gateway respondeu sem texto válido."
    return texto, None


def _imprimir_sse(texto: str, prefixo: str) -> str | None:
    """Imprime os tokens de um corpo SSE conforme parseia.

    Retorna o texto completo, ou None se o corpo nao for SSE (nesse
    caso nada e impresso e o chamador tenta o JSON simples).
    """
    return _imprimir_sse_linhas(texto.splitlines(), prefixo)


def _imprimir_sse_linhas(linhas, prefixo: str) -> str | None:
    partes: list[str] = []
    viu_sse = False
    terminou = False
    comecou = False
    for linha in linhas:
        linha = linha.strip()
        if not linha.startswith("data:"):
            continue
        viu_sse = True
        dado = linha[5:].strip()
        if dado == "[DONE]":
            terminou = True
            break
        try:
            evento = json.loads(dado)
        except ValueError as exc:
            raise RuntimeError("O gateway retornou streaming inválido.") from exc
        if not isinstance(evento, dict):
            raise TypeError("O gateway retornou streaming inválido.")
        if evento.get("error"):
            raise RuntimeError("O gateway informou erro durante a resposta.")
        escolhas = evento.get("choices")
        if escolhas == []:
            continue
        if not isinstance(escolhas, list) or not escolhas or not isinstance(escolhas[0], dict):
            raise RuntimeError("O gateway retornou streaming inválido.")
        escolha = escolhas[0]
        motivo = escolha.get("finish_reason")
        if motivo == "length":
            raise RuntimeError("Resposta incompleta: limite de tokens atingido.")
        if motivo not in (None, "stop"):
            raise RuntimeError("Resposta interrompida pelo modelo.")
        if motivo == "stop":
            terminou = True
        delta = escolha.get("delta") or {}
        if not isinstance(delta, dict):
            raise TypeError("O gateway retornou streaming inválido.")
        delta = delta.get("content") or ""
        if delta:
            if not comecou:
                print(prefixo, end="", flush=True)
                comecou = True
            print(_terminal_seguro(delta), end="", flush=True)
            partes.append(str(delta))
    if not viu_sse:
        return None
    if not terminou:
        raise RuntimeError("Resposta interrompida antes da confirmação de término.")
    if comecou:
        print()
    else:
        print(prefixo)
    return "".join(partes)


def _mostrar_resposta_chat(resp: httpx.Response, prefixo: str = "") -> tuple[str | None, str | None]:
    """Imprime SSE em tempo real; devolve texto original para o histórico."""
    try:
        if resp.status_code == 200 and "text/event-stream" in resp.headers.get("content-type", "").lower():
            texto = _imprimir_sse_linhas(resp.iter_lines(), prefixo)
            return (texto, None) if texto is not None else (None, "Resposta de streaming inválida.")
        if not resp.is_closed:
            resp.read()
        if resp.status_code != 200:
            return _texto_resposta(resp)
        texto = _imprimir_sse(resp.text, prefixo)
        if texto is not None:
            return texto, None
        texto, erro = _texto_resposta(resp)
        if texto is not None:
            print(f"{prefixo}{_terminal_seguro(texto)}")
        return texto, erro
    except httpx.HTTPError:
        return None, "A conexão com o gateway foi interrompida durante a resposta."
    except (RuntimeError, TypeError) as exc:
        return None, str(exc)
    finally:
        resp.close()
        cliente = resp.extensions.get("passe1_cliente")
        if cliente is not None:
            cliente.close()


def _imagens(caminhos: list[str]) -> list[dict]:
    """Lê imagens locais com limite total, identificando o formato pelos bytes."""
    limite = 4 * 1024 * 1024
    total = 0
    partes = []
    for nome in caminhos:
        try:
            caminho = Path(nome)
            if not caminho.is_file():
                raise OSError("Não é arquivo regular.")
            with caminho.open("rb") as arquivo:
                dados = arquivo.read(limite - total + 1)
        except OSError as exc:
            raise ValueError(f"Não consegui ler a imagem: {nome}.") from exc
        total += len(dados)
        if total > limite:
            raise ValueError("As imagens ultrapassam o limite total de 4 MiB.")
        if dados.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif dados.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif dados.startswith((b"GIF87a", b"GIF89a")):
            mime = "image/gif"
        elif dados.startswith(b"RIFF") and dados[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            raise ValueError(f"Formato de imagem inválido em {nome}. Use PNG, JPEG, GIF ou WebP.")
        codificado = base64.b64encode(dados).decode("ascii")
        partes.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{codificado}"}})
    return partes


def _conteudo_mensagem(texto: str, imagens: list[dict] | None = None):
    return [{"type": "text", "text": texto}, *imagens] if imagens else texto


def _uma_pergunta(
    base: str, chave: str, modelo: str, texto: str, sistema: str | None = None,
    imagens: list[dict] | None = None,
) -> int:
    mensagens: list = []
    if sistema:
        mensagens.append({"role": "system", "content": sistema})
    mensagens.append({"role": "user", "content": _conteudo_mensagem(texto, imagens)})
    try:
        resp = _post_chat(base, chave, modelo, mensagens)
    except httpx.HTTPError as exc:
        print(f"Nao consegui falar com o gateway ({exc.__class__.__name__}).")
        return 1
    _, erro = _mostrar_resposta_chat(resp)
    if erro is not None:
        print(_terminal_seguro(erro))
        return 1
    return 0


def _arquivo_historico() -> Path:
    return _dir_config() / "history"


def _configurar_historico() -> Callable[[], None] | None:
    """Liga o historico do terminal (setas); None se readline nao existir."""
    try:
        import readline
    except ImportError:
        return None
    try:
        alvo = _arquivo_historico()
        alvo.parent.mkdir(parents=True, exist_ok=True)
        try:
            readline.read_history_file(str(alvo))
        except OSError:
            pass
        try:
            readline.set_history_length(500)
        except (OSError, ValueError):
            pass

        def _salvar() -> None:
            try:
                readline.write_history_file(str(alvo))
            except OSError:
                pass

        return _salvar
    except OSError:
        return None


def _dirs_skills() -> list[Path]:
    casa = _hoje()
    return [
        casa / ".claude" / "skills",
        casa / ".codex" / "skills",
        _dir_config() / "skills",
    ]


def _listar_skills() -> dict[str, Path]:
    """Nome (nome do diretorio) para SKILL.md; o dir do passe1 ganha empates."""
    achadas: dict[str, Path] = {}
    for base in _dirs_skills():
        try:
            pastas = sorted(p for p in base.iterdir() if p.is_dir())
        except OSError:
            continue
        for pasta in pastas:
            alvo = pasta / "SKILL.md"
            if alvo.is_file():
                achadas[pasta.name] = alvo
    return achadas


def _ler_skill(nome: str) -> str | None:
    alvo = _listar_skills().get(nome)
    if alvo is None:
        return None
    try:
        texto = alvo.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return texto or None


def _arquivo_skill_ativa() -> Path:
    return _dir_config() / "skill"


def _ler_skill_ativa() -> str | None:
    try:
        texto = _arquivo_skill_ativa().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return texto or None


def _guardar_skill_ativa(nome: str) -> None:
    destino = _arquivo_skill_ativa()
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(nome.strip() + "\n", encoding="utf-8")


def _limpar_skill_ativa() -> None:
    try:
        _arquivo_skill_ativa().unlink()
    except OSError:
        pass


def cmd_skills(args: argparse.Namespace) -> int:
    _ = args
    achadas = _listar_skills()
    if not achadas:
        print("Nenhuma skill encontrada.")
        print("Coloque um SKILL.md em ~/.config/passe1/skills/NOME/SKILL.md,")
        print("ou reaproveite as skills locais dos seus editores.")
        return 0
    ativa = _ler_skill_ativa()
    print(f"Skills disponiveis: {len(achadas)}")
    for nome in sorted(achadas):
        marca = " (ativa)" if nome == ativa else ""
        print(f"  - {nome}{marca}")
    return 0


AJUDA_CHAT = (
    "Conversa com o passe. Comandos: /sair termina, /limpar zera o contexto,"
    " /skill NOME ativa uma skill, /model [ID] mostra ou troca o modelo."
)


def _msg_system(sistema: str | None) -> list:
    if sistema:
        return [{"role": "system", "content": sistema}]
    return []


def _trocar_skill_repl(
    texto: str, nome_atual: str | None, sistema_atual: str | None, historico: list
) -> tuple[str | None, str | None, list]:
    """Trata o /skill no REPL; devolve (nome, sistema, historico)."""
    partes = texto.split(None, 1)
    if len(partes) == 1:
        if nome_atual:
            print(f"Skill ativa: {nome_atual}.")
        else:
            print("Nenhuma skill ativa. Use: /skill NOME (veja com: passe1 skills).")
        return nome_atual, sistema_atual, historico
    pedido = partes[1].strip()
    if pedido == "off":
        msg = {"role": "system", "content": SISTEMA_PADRAO}
        if historico and historico[0].get("role") == "system":
            historico[0] = msg
        else:
            historico.insert(0, msg)
        _limpar_skill_ativa()
        print("Skill desativada.")
        return None, SISTEMA_PADRAO, historico
    conteudo = _ler_skill(pedido)
    if conteudo is None:
        print(f"Skill '{pedido}' nao encontrada. Veja com: passe1 skills.")
        return nome_atual, sistema_atual, historico
    msg = {"role": "system", "content": conteudo}
    if historico and historico[0].get("role") == "system":
        historico[0] = msg
    else:
        historico.insert(0, msg)
    _guardar_skill_ativa(pedido)
    print(f"Skill '{pedido}' ativada.")
    return pedido, conteudo, historico


def _repl(
    base: str,
    chave: str,
    modelo: str,
    ler: Callable[[str], str],
    skill: str | None = None,
    imagens: list[dict] | None = None,
) -> int:
    salvar_historico = _configurar_historico()
    try:
        print(AJUDA_CHAT)
        nome_skill = skill or _ler_skill_ativa()
        sistema = _ler_skill(nome_skill) if nome_skill else SISTEMA_PADRAO
        if nome_skill is not None and sistema is None:
            # So chega aqui pela skill salva: a do --skill o cmd_chat ja validou.
            print(f"A skill salva '{nome_skill}' sumiu; seguindo sem skill.")
            _limpar_skill_ativa()
            nome_skill = None
            sistema = SISTEMA_PADRAO
        if skill is not None and nome_skill is not None:
            _guardar_skill_ativa(nome_skill)
        if nome_skill is not None:
            print(f"Skill ativa: {nome_skill}.")
        print(f"Modelo: {modelo}.")
        historico: list = _msg_system(sistema)
        while True:
            try:
                linha = ler("voce: ")
            except (EOFError, KeyboardInterrupt):
                print("\nAte logo!")
                return 0
            texto = linha.strip()
            if not texto:
                continue
            if texto in ("/sair", "/quit", "/exit"):
                print("Ate logo!")
                return 0
            if texto == "/limpar":
                historico = _msg_system(sistema)
                print("Contexto zerado. Pode comecar de novo.")
                continue
            if texto == "/skill" or texto.startswith("/skill "):
                nome_skill, sistema, historico = _trocar_skill_repl(
                    texto, nome_skill, sistema, historico
                )
                continue
            if texto == "/model" or texto.startswith("/model "):
                partes = texto.split(None, 1)
                if len(partes) == 1:
                    print(f"Modelo atual: {modelo}.")
                else:
                    modelo = partes[1].strip()
                    print(f"Modelo agora: {modelo}.")
                continue
            historico.append({"role": "user", "content": _conteudo_mensagem(texto, imagens)})
            try:
                resp = _post_chat(base, chave, modelo, historico)
            except httpx.HTTPError as exc:
                historico.pop()
                print(f"Nao consegui falar com o gateway ({exc.__class__.__name__}).")
                continue
            resposta, erro = _mostrar_resposta_chat(resp, prefixo="passe1: ")
            if erro is not None:
                historico.pop()
                print(_terminal_seguro(erro))
                continue
            imagens = None
            historico.append({"role": "assistant", "content": resposta})
    finally:
        if salvar_historico is not None:
            salvar_historico()


def cmd_chat(args: argparse.Namespace) -> int:
    chave = _ler_chave(_base_url(args.base_url))
    if chave is None:
        return _sem_login()
    modelo = args.model or _modelo_padrao()
    base = _base_url(args.base_url)
    imagens = _imagens(args.image or [])
    sistema = SISTEMA_PADRAO
    if args.skill:
        sistema = _ler_skill(args.skill)
        if sistema is None:
            print(f"Skill '{args.skill}' nao encontrada. Veja com: passe1 skills.")
            return 1
    if args.mensagem:
        return _uma_pergunta(base, chave, modelo, " ".join(args.mensagem), sistema, imagens)
    if not sys.stdin.isatty():
        texto = sys.stdin.read().strip()
        if not texto:
            print("Nada para enviar: passe a mensagem ou digite no terminal.")
            return 1
        return _uma_pergunta(base, chave, modelo, texto, sistema, imagens)
    return _repl(base, chave, modelo, input, args.skill, imagens)


def cmd_agent(args: argparse.Namespace) -> int:
    import agente

    if os.name == "nt":
        from passe1_engine.launcher import iniciar
        return iniciar([], agente=args)
    if (not args.tarefa and not args.model and not args.session and not args.sandbox and not args.image and not args.base_url
            and sys.stdin.isatty() and sys.stdout.isatty()):
        from passe1_engine.launcher import iniciar
        return iniciar([])
    chave = _ler_chave(_base_url(args.base_url))
    if chave is None:
        return _sem_login()
    import arquivos_seguros
    try:
        arquivos_seguros._disponivel()
    except RuntimeError as exc:
        print(str(exc))
        return 1
    if args.sandbox and (sys.platform != "darwin" or not os.access("/usr/bin/sandbox-exec", os.X_OK)):
        print("Sandbox de comandos indisponível nesta plataforma; nenhum pedido foi enviado.")
        return 1
    tarefa = " ".join(args.tarefa).strip()
    interativo = not tarefa
    if interativo and not sys.stdin.isatty():
        print("Informe uma tarefa ou use um terminal interativo.")
        return 1
    modelo = args.model or _modelo_padrao()
    imagens = _imagens(args.image or [])
    if args.session:
        from sessao import Sessao
        try:
            with Sessao(_dir_config(), args.session, Path.cwd().resolve(), _base_url(args.base_url), modelo, chave) as sessao:
                sucesso, erro = agente.executar(_base_url(args.base_url), chave, modelo, tarefa, _cliente, input, interativo=interativo, imagens=imagens, sessao=sessao, usar_sandbox=args.sandbox)
        except OSError as exc:
            print(f"Não foi possível acessar a sessão ({type(exc).__name__}).")
            return 1
    else:
        sucesso, erro = agente.executar(_base_url(args.base_url), chave, modelo, tarefa, _cliente, input, interativo=interativo, imagens=imagens, usar_sandbox=args.sandbox)
    if erro:
        print(_terminal_seguro(erro))
    return 0 if sucesso else 1


def _backup(caminho: Path) -> Path:
    copia = caminho.with_suffix(caminho.suffix + ".bak")
    shutil.copy2(caminho, copia)
    return copia


BLOCO_CODEX = """\
# gerenciado por "passe1 setup codex" (edite por sua conta e risco)
model_provider = "passe1"

[model_providers.passe1]
name = "Passe1"
base_url = "{base}"
env_key = "PASSE1_API_KEY"
"""


def _setup_codex(base: str) -> int:
    alvo = _hoje() / ".codex" / "config.toml"
    if alvo.exists() and "model_providers.passe1" in alvo.read_text(encoding="utf-8"):
        print("Codex ja aponta para o Passe1. Nada a fazer.")
        return 0
    alvo.parent.mkdir(parents=True, exist_ok=True)
    if alvo.exists():
        print(f"Backup do config atual em {_backup(alvo)}.")
        with alvo.open("a", encoding="utf-8") as fh:
            fh.write("\n" + BLOCO_CODEX.format(base=base))
    else:
        alvo.write_text(BLOCO_CODEX.format(base=base), encoding="utf-8")
    print("Codex configurado para usar o Passe1.")
    print("Falta exportar a chave no terminal:")
    print("  export PASSE1_API_KEY=$(cat ~/.config/passe1/key)")
    return 0



def cmd_setup(args: argparse.Namespace) -> int:
    chave = _ler_chave(_base_url(args.base_url))
    if chave is None:
        return _sem_login()
    base = _base_url(args.base_url)
    alvo = (args.alvo or "").lower()
    if alvo == "codex":
        return _setup_codex(base)
    print(f"Alvo desconhecido: '{args.alvo}'. Use: codex.")
    return 1


def montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="passe1", description="CLI do Passe1: seu passe de IA no terminal."
    )
    parser.add_argument("--versao", action="version", version=f"%(prog)s {VERSAO}")
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"Base do gateway (padrao: {BASE_URL_PADRAO}).",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_login = sub.add_parser("login", help="Entrar na conta ou usar uma chave de API.")
    p_login.add_argument("chave", nargs="?", help="Chave de API (preferível: --key).")
    p_login.add_argument("--key", action="store_true", help="Entrar com chave de API em vez da conta.")
    p_login.set_defaults(func=cmd_login)

    p_chat = sub.add_parser("chat", help="Conversar no terminal.")
    p_chat.add_argument("mensagem", nargs="*", help="Mensagem unica (sem ela, abre o chat).")
    p_chat.add_argument("--model", default=None, help=f"Modelo (padrao: {MODELO_PADRAO}).")
    p_chat.add_argument("--image", action="append", help="Imagem local (repita para anexar mais de uma).")
    p_chat.add_argument("--skill", default=None, help="Skill (SKILL.md) para guiar a conversa.")
    p_chat.set_defaults(func=cmd_chat)

    p_agent = sub.add_parser("agent", help="Trabalhar em código no projeto atual.")
    p_agent.add_argument("tarefa", nargs="*", help="Tarefa a executar no projeto.")
    p_agent.add_argument("--model", default=None, help="Modelo com suporte a ferramentas.")
    p_agent.add_argument("--sandbox", action="store_true", help="Bloquear escrita e rede dos comandos aprovados (macOS; somente leitura).")
    p_agent.add_argument("--session", help="Nome para salvar e retomar o histórico local do agente.")
    p_agent.add_argument("--image", action="append", help="Imagem local (repita para anexar mais de uma).")
    p_agent.set_defaults(func=cmd_agent)

    p_config = sub.add_parser("config", help="Consultar ou configurar o modelo padrão.")
    p_config.add_argument("opcao", choices=["model"])
    p_config.add_argument("valor", nargs="?", help="Novo identificador do modelo.")
    p_config.set_defaults(func=cmd_config)

    p_keys = sub.add_parser("keys", help="Listar ou criar chaves da conta.")
    p_keys.add_argument("acao", nargs="?", choices=["list", "create"], default="list")
    p_keys.add_argument("--json", action="store_true", help="Listar chaves em JSON.")
    p_keys.set_defaults(func=cmd_keys)

    p_usage = sub.add_parser("usage", help="Mostrar uso e custo por modelo.")
    p_usage.add_argument("--dias", type=int, choices=range(1, 91), default=7, metavar="1..90")
    p_usage.add_argument("--json", action="store_true", help="Mostrar resposta em JSON.")
    p_usage.set_defaults(func=cmd_usage)

    p_setup = sub.add_parser("setup", help="Apontar um harness para o Passe1.")
    p_setup.add_argument("alvo", help="Harness a configurar: codex.")
    p_setup.set_defaults(func=cmd_setup)

    p_models = sub.add_parser("models", help="Listar os modelos disponiveis.")
    p_models.add_argument("--json", action="store_true", help="Saida em JSON (para scripts).")
    p_models.set_defaults(func=cmd_models)

    p_skills = sub.add_parser("skills", help="Listar skills locais (SKILL.md).")
    p_skills.set_defaults(func=cmd_skills)

    p_status = sub.add_parser("status", help="Mostrar saldo da conta ou uso da chave.")
    p_status.add_argument("--json", action="store_true", help="Saida em JSON (para scripts).")
    p_status.set_defaults(func=cmd_status)

    p_logout = sub.add_parser("logout", help="Encerrar sessão e apagar credenciais locais.")
    p_logout.set_defaults(func=cmd_logout)
    return parser


def main(argv: list[str] | None = None) -> int:
    argumentos = sys.argv[1:] if argv is None else argv
    if not argumentos and sys.stdin.isatty() and sys.stdout.isatty():
        from passe1_engine.launcher import iniciar
        return iniciar([])
    if not argumentos:
        argumentos = ["agent"]
    args = montar_parser().parse_args(argumentos)
    try:
        return int(args.func(args))
    except ValueError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
