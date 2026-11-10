"""Testes da CLI passe1 (fatias 1 e 2). Sem rede: todo HTTP passa por
httpx.MockTransport injetado em passe1._transporte. Config e home sao
isolados por teste via PASSE1_CONFIG_DIR e PASSE1_HOME.
"""
from __future__ import annotations

import datetime
import json
import os
import stat
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import passe1

# Montada por partes de proposito: um literal de chave inteiro aqui seria
# redigido pelas ferramentas, e este valor e ficticio, so para teste.
CHAVE = passe1.PREFIXO_CHAVE + "teste-abc123"
MODELOS = {
    "object": "list",
    "data": [
        {"id": "deepseek-v4.1-flash", "object": "model"},
        {"id": "glm-5.3-flash", "object": "model"},
        {"id": "mimo-v2.6-flash", "object": "model"},
    ],
}


def _erro(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": code, "message": message, "type": "t"}}
    )


@pytest.fixture(autouse=True)
def isolamento(tmp_path, monkeypatch):
    """Cada teste ganha config dir e home vazios; rede sempre mockada."""
    monkeypatch.setenv("PASSE1_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PASSE1_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PASSE1_BASE_URL", "https://gateway.teste/v1")
    monkeypatch.setattr(passe1, "_transporte", None)
    yield
    passe1._transporte = None


def _mockar(handler) -> None:
    passe1._transporte = httpx.MockTransport(handler)


def _logar_direto() -> Path:
    passe1._guardar_chave(CHAVE)
    return passe1._arquivo_chave()


# login


def test_login_ok_guarda_chave_com_permissao_600(capsys):
    def handler(request):
        assert request.headers["authorization"] == f"Bearer {CHAVE}"
        return httpx.Response(200, json=MODELOS)

    _mockar(handler)
    assert passe1.main(["login", CHAVE]) == 0

    arquivo = passe1._arquivo_chave()
    assert arquivo.read_text(encoding="utf-8").strip() == CHAVE
    assert stat.S_IMODE(arquivo.stat().st_mode) == 0o600
    assert stat.S_IMODE(arquivo.parent.stat().st_mode) == 0o700
    assert "Login ok" in capsys.readouterr().out


def test_login_formato_invalido_nem_toca_a_rede(capsys):
    def handler(request):  # pragma: no cover - nao pode ser chamado
        raise AssertionError("tocou a rede com chave invalida")

    _mockar(handler)
    assert passe1.main(["login", "chave-errada"]) == 1
    assert "Formato invalido" in capsys.readouterr().out
    assert not passe1._arquivo_chave().exists()


def test_login_chave_desconhecida_nao_guarda(capsys):
    _mockar(lambda req: _erro(401, "invalid_api_key", "desconhecida"))
    assert passe1.main(["login", CHAVE]) == 1
    assert "desconhecida" in capsys.readouterr().out.lower()
    assert not passe1._arquivo_chave().exists()


def test_login_passe_expirado_avisa_renovacao(capsys):
    _mockar(lambda req: _erro(401, "pass_expired", "expirado"))
    assert passe1.main(["login", CHAVE]) == 1
    assert "expirado" in capsys.readouterr().out.lower()


def test_login_sem_rede_falha_com_mensagem(capsys):
    def handler(request):
        raise httpx.ConnectError("sem rota")

    _mockar(handler)
    assert passe1.main(["login", CHAVE]) == 1
    assert "Nao consegui falar" in capsys.readouterr().out


def test_login_pede_chave_quando_nao_passada(monkeypatch, capsys):
    monkeypatch.setattr(passe1.getpass, "getpass", lambda prompt: CHAVE)
    _mockar(lambda req: httpx.Response(200, json=MODELOS))
    assert passe1.main(["login", "--key"]) == 0
    assert passe1._ler_chave() == CHAVE


# logout


def test_logout_apaga_chave_guardada(capsys):
    _logar_direto()
    assert passe1.main(["logout"]) == 0
    assert not passe1._arquivo_chave().exists()
    assert "Logout ok" in capsys.readouterr().out


def test_logout_sem_login_e_ok(capsys):
    assert passe1.main(["logout"]) == 0
    assert "deslogado" in capsys.readouterr().out


# models


def test_models_sem_login_pede_login(capsys):
    assert passe1.main(["models"]) == 1
    assert "passe1 login" in capsys.readouterr().out


def test_models_ok_mostra_janela_quando_houver(capsys):
    _logar_direto()

    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "a", "object": "model", "context_window": 128000},
                    {"id": "b", "object": "model"},
                ],
            },
        )

    _mockar(handler)
    assert passe1.main(["models"]) == 0
    saida = capsys.readouterr().out
    assert "2" in saida
    assert "a (janela: 128000 tokens)" in saida
    assert "  - b\n" in saida


def test_models_chave_revogada(capsys):
    _logar_direto()
    _mockar(lambda req: _erro(401, "api_key_revoked", "revogada"))
    assert passe1.main(["models"]) == 1
    assert "revogada" in capsys.readouterr().out.lower()


def test_models_mostra_visao_quando_houver(capsys):
    _logar_direto()

    def handler(request):
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "a", "object": "model", "vision": True},
                    {"id": "b", "object": "model", "vision": False},
                ],
            },
        )

    _mockar(handler)
    assert passe1.main(["models"]) == 0
    saida = capsys.readouterr().out
    assert "a [visao]" in saida
    assert "[visao]" not in saida.split("  - b")[1].split("\n")[0]


def test_models_json_imprime_lista_crua(capsys):
    _logar_direto()
    _mockar(lambda req: httpx.Response(200, json=MODELOS))
    assert passe1.main(["models", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == MODELOS["data"]


# status


ME = {
    "object": "key_info",
    "valida": True,
    "passe": {
        "ativo": True,
        "tier": "P",
        "expira_em": 1798761600.0,
        "dias_restantes": 365,
    },
    "uso": {
        "ultimas_24h": {"requisicoes": 10, "tokens": 2500, "erros": 0},
        "ultimos_7d": {"requisicoes": 42, "tokens": 99999, "erros": 1},
    },
}


def test_status_sem_login_pede_login(capsys):
    assert passe1.main(["status"]) == 1
    assert "passe1 login" in capsys.readouterr().out


def test_status_ok_mostra_passe_e_uso(capsys):
    _logar_direto()

    def handler(request):
        assert request.url.path == "/v1/me"
        return httpx.Response(200, json=ME)

    _mockar(handler)
    assert passe1.main(["status"]) == 0
    saida = capsys.readouterr().out
    assert "Passe valido" in saida
    assert "Plano: P." in saida
    assert "365 dias" in saida
    esperada = datetime.datetime.fromtimestamp(1798761600.0).strftime("%d/%m/%Y")
    assert esperada in saida
    assert "10 requisicoes" in saida
    assert "2500 tokens" in saida
    assert "42 requisicoes" in saida
    assert "1 erro." in saida


def test_status_singular_e_sem_expiracao(capsys):
    _logar_direto()
    corpo = {
        "object": "key_info",
        "valida": True,
        "passe": {"ativo": True, "tier": None, "expira_em": None, "dias_restantes": 1},
        "uso": {
            "ultimas_24h": {"requisicoes": 1, "tokens": 1, "erros": 1},
            "ultimos_7d": {"requisicoes": 0, "tokens": 0, "erros": 0},
        },
    }
    _mockar(lambda req: httpx.Response(200, json=corpo))
    assert passe1.main(["status"]) == 0
    saida = capsys.readouterr().out
    assert "1 dia." in saida
    assert "1 requisicao," in saida
    assert "Plano: -." in saida


def test_status_passe_expirado(capsys):
    _logar_direto()
    _mockar(lambda req: _erro(401, "pass_expired", "expirado"))
    assert passe1.main(["status"]) == 1
    assert "expirado" in capsys.readouterr().out.lower()


def test_status_chave_revogada(capsys):
    _logar_direto()
    _mockar(lambda req: _erro(401, "api_key_revoked", "revogada"))
    assert passe1.main(["status"]) == 1
    assert "revogada" in capsys.readouterr().out.lower()


def test_status_json_imprime_corpo_cru(capsys):
    _logar_direto()
    _mockar(lambda req: httpx.Response(200, json=ME))
    assert passe1.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == ME


# chat


def _resposta_chat(texto: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": texto}}]},
    )


def test_chat_sem_login_pede_login(capsys):
    assert passe1.main(["chat", "oi"]) == 1
    assert "passe1 login" in capsys.readouterr().out


def test_chat_mensagem_unica_imprime_resposta(capsys):
    _logar_direto()

    def handler(request):
        corpo = json.loads(request.content.decode())
        assert corpo["model"] == passe1.MODELO_PADRAO
        assert corpo["messages"] == [
            {"role": "system", "content": passe1.SISTEMA_PADRAO},
            {"role": "user", "content": "oi tudo bem"},
        ]
        assert corpo["stream"] is True
        return _resposta_chat("tudo otimo!")

    _mockar(handler)
    assert passe1.main(["chat", "oi", "tudo", "bem"]) == 0
    assert "tudo otimo!" in capsys.readouterr().out


def _resposta_sse(*tokens: str) -> httpx.Response:
    linhas = []
    for tok in tokens:
        linhas.append(
            "data: " + json.dumps({"choices": [{"delta": {"content": tok}}]})
        )
    linhas.append("data: [DONE]")
    return httpx.Response(
        200, content="\n".join(linhas), headers={"content-type": "text/event-stream"}
    )


def test_chat_stream_imprime_tokens_juntos(capsys):
    _logar_direto()

    def handler(request):
        corpo = json.loads(request.content.decode())
        assert corpo["stream"] is True
        return _resposta_sse("tudo ", "otimo", "!")

    _mockar(handler)
    assert passe1.main(["chat", "oi"]) == 0
    assert "tudo otimo!" in capsys.readouterr().out


def test_chat_stream_vazio_nao_quebra(capsys):
    _logar_direto()
    _mockar(lambda req: _resposta_sse())
    assert passe1.main(["chat", "oi"]) == 0


def test_chat_repl_stream_mantem_contexto(capsys):
    _logar_direto()
    corpos = []

    def handler(request):
        corpos.append(json.loads(request.content.decode()))
        return _resposta_sse("eco!")

    _mockar(handler)
    entradas = iter(["ola", "/sair"])
    assert passe1._repl("https://x", CHAVE, "m", lambda prompt: next(entradas)) == 0
    assert corpos[0]["messages"] == [
        {"role": "system", "content": passe1.SISTEMA_PADRAO},
        {"role": "user", "content": "ola"},
    ]
    assert "passe1: eco!" in capsys.readouterr().out


def test_chat_respeita_modelo_do_flag(capsys):
    _logar_direto()

    def handler(request):
        corpo = json.loads(request.content.decode())
        assert corpo["model"] == "mimo-v2.6-flash"
        return _resposta_chat("ok")

    _mockar(handler)
    assert passe1.main(["chat", "--model", "mimo-v2.6-flash", "oi"]) == 0


def test_chat_erro_do_gateway_vira_mensagem(capsys):
    _logar_direto()
    _mockar(lambda req: _erro(429, "daily_token_cap_exceeded", "teto diario"))
    assert passe1.main(["chat", "oi"]) == 1
    assert "teto diario" in capsys.readouterr().out


def test_chat_repl_conversa_ate_sair(capsys, monkeypatch):
    _logar_direto()
    _mockar(lambda req: _resposta_chat("eco!"))
    entradas = iter(["ola", "/limpar", "de novo", "/sair"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert passe1._repl("https://x", CHAVE, "m", lambda prompt: next(entradas)) == 0
    saida = capsys.readouterr().out
    assert saida.count("passe1: eco!") == 2
    assert "Contexto zerado" in saida


def test_chat_repl_eof_termina_limpo(capsys):
    def ler(prompt):
        raise EOFError

    assert passe1._repl("https://x", CHAVE, "m", ler) == 0
    assert "Ate logo" in capsys.readouterr().out


def test_chat_repl_model_mostra_e_troca(capsys):
    vistos = []

    def handler(request):
        vistos.append(request.content and "m2" in request.content.decode())
        return _resposta_chat("eco!")

    _logar_direto()
    _mockar(handler)
    entradas = iter(["/model", "/model m2", "oi", "/sair"])
    assert passe1._repl("https://x", CHAVE, "m1", lambda prompt: next(entradas)) == 0
    saida = capsys.readouterr().out
    assert "Modelo: m1." in saida
    assert "Modelo atual: m1." in saida
    assert "Modelo agora: m2." in saida
    assert vistos == [True]


def test_chat_repl_falha_de_rede_nao_mata_a_sessao(capsys):
    chamadas = {"n": 0}

    def handler(request):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise httpx.ConnectError("caiu")
        return _resposta_chat("voltei!")

    _mockar(handler)
    entradas = iter(["oi", "oi", "/sair"])
    assert passe1._repl("https://x", CHAVE, "m", lambda prompt: next(entradas)) == 0
    saida = capsys.readouterr().out
    assert "Nao consegui falar" in saida
    assert "passe1: voltei!" in saida


# historico


def test_repl_grava_historico_no_config_dir():
    pytest.importorskip("readline")
    entradas = iter(["/sair"])
    assert passe1._repl("https://x", CHAVE, "m", lambda prompt: next(entradas)) == 0
    assert passe1._arquivo_historico().exists()
    assert passe1._arquivo_historico().parent == Path(os.environ["PASSE1_CONFIG_DIR"])


def test_configurar_historico_sem_readline_nao_quebra(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def sem_readline(nome, *args, **kwargs):
        if nome == "readline":
            raise ImportError("sem readline")
        return real_import(nome, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", sem_readline)
    assert passe1._configurar_historico() is None


# setup


def test_setup_sem_login_pede_login(capsys):
    assert passe1.main(["setup", "codex"]) == 1
    assert "passe1 login" in capsys.readouterr().out


def test_setup_alvo_desconhecido_lista_opcoes(capsys):
    _logar_direto()
    assert passe1.main(["setup", "navegador"]) == 1
    saida = capsys.readouterr().out
    assert "claude" not in saida and "codex" in saida


def test_setup_codex_novo_cria_config(capsys):
    _logar_direto()
    assert passe1.main(["setup", "codex"]) == 0
    alvo = Path(os.environ["PASSE1_HOME"]) / ".codex" / "config.toml"
    texto = alvo.read_text(encoding="utf-8")
    assert "model_providers.passe1" in texto
    assert "https://gateway.teste/v1" in texto
    assert "PASSE1_API_KEY" in capsys.readouterr().out


def test_setup_codex_existente_faz_backup_e_preserva(capsys):
    _logar_direto()
    alvo = Path(os.environ["PASSE1_HOME"]) / ".codex" / "config.toml"
    alvo.parent.mkdir(parents=True)
    alvo.write_text('model = "x"\n', encoding="utf-8")
    assert passe1.main(["setup", "codex"]) == 0
    assert (alvo.parent / "config.toml.bak").read_text(encoding="utf-8") == 'model = "x"\n'
    texto = alvo.read_text(encoding="utf-8")
    assert 'model = "x"' in texto and "model_providers.passe1" in texto


def test_setup_codex_duas_vezes_nao_duplica(capsys):
    _logar_direto()
    assert passe1.main(["setup", "codex"]) == 0
    assert passe1.main(["setup", "codex"]) == 0
    alvo = Path(os.environ["PASSE1_HOME"]) / ".codex" / "config.toml"
    assert alvo.read_text(encoding="utf-8").count("model_providers.passe1") == 1


def test_setup_removido_nao_altera_configuracao(capsys):
    _logar_direto()
    alvo = Path(os.environ["PASSE1_HOME"]) / ".claude" / "settings.json"
    alvo.parent.mkdir(parents=True)
    alvo.write_text('{"theme":"escuro"}', encoding="utf-8")
    assert passe1.main(["setup", "claude"]) == 1
    assert alvo.read_text(encoding="utf-8") == '{"theme":"escuro"}'


# skills


def _criar_skill(base: Path, nome: str, texto: str = "voce e um revisor.") -> Path:
    alvo = base / nome / "SKILL.md"
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text(texto, encoding="utf-8")
    return alvo


def test_skills_vazio_avisa(capsys):
    assert passe1.main(["skills"]) == 0
    assert "Nenhuma skill" in capsys.readouterr().out


def test_skills_lista_os_tres_dirs(capsys):
    casa = Path(os.environ["PASSE1_HOME"])
    conf = Path(os.environ["PASSE1_CONFIG_DIR"])
    _criar_skill(casa / ".claude" / "skills", "a-claude")
    _criar_skill(casa / ".codex" / "skills", "b-codex")
    _criar_skill(conf / "skills", "c-passe1")
    assert passe1.main(["skills"]) == 0
    saida = capsys.readouterr().out
    assert "3" in saida
    assert "a-claude" in saida
    assert "b-codex" in saida
    assert "c-passe1" in saida


def test_skills_dir_do_passe1_ganha_empate():
    casa = Path(os.environ["PASSE1_HOME"])
    conf = Path(os.environ["PASSE1_CONFIG_DIR"])
    _criar_skill(casa / ".claude" / "skills", "dupla", "texto claude")
    _criar_skill(conf / "skills", "dupla", "texto passe1")
    assert passe1._ler_skill("dupla") == "texto passe1"


def test_chat_skill_flag_injeta_system(capsys):
    _logar_direto()
    conf = Path(os.environ["PASSE1_CONFIG_DIR"])
    _criar_skill(conf / "skills", "revisor", "revise com carinho.")

    def handler(request):
        corpo = json.loads(request.content.decode())
        assert corpo["messages"][0] == {"role": "system", "content": "revise com carinho."}
        assert corpo["messages"][1] == {"role": "user", "content": "oi"}
        return _resposta_chat("ok")

    _mockar(handler)
    assert passe1.main(["chat", "--skill", "revisor", "oi"]) == 0


def test_chat_skill_inexistente_erro(capsys):
    _logar_direto()
    assert passe1.main(["chat", "--skill", "fantasma", "oi"]) == 1
    assert "nao encontrada" in capsys.readouterr().out


def test_chat_sem_skill_usa_sistema_padrao_ptbr():
    """Sem --skill, a CLI injeta o system padrao que trava o pt-BR: sem isso
    o modelo pequeno vazava ingles/espanhol/frances no chat do terminal."""
    _logar_direto()

    def handler(request):
        corpo = json.loads(request.content.decode())
        assert corpo["messages"][0]["role"] == "system"
        assert "português do Brasil" in corpo["messages"][0]["content"]
        return _resposta_chat("ok")

    _mockar(handler)
    assert passe1.main(["chat", "oi"]) == 0


def test_repl_skill_ativa_persiste_e_recarrega(capsys):
    _logar_direto()
    corpos = []

    def handler(request):
        corpos.append(json.loads(request.content.decode()))
        return _resposta_chat("eco!")

    _mockar(handler)
    conf = Path(os.environ["PASSE1_CONFIG_DIR"])
    _criar_skill(conf / "skills", "revisor", "revise com carinho.")
    entradas = iter(["/skill", "/skill revisor", "/skill", "oi", "/sair"])
    assert passe1._repl("https://x", CHAVE, "m", lambda prompt: next(entradas)) == 0
    saida = capsys.readouterr().out
    assert "Nenhuma skill ativa" in saida
    assert "Skill 'revisor' ativada." in saida
    assert "Skill ativa: revisor." in saida
    assert corpos[0]["messages"][0] == {"role": "system", "content": "revise com carinho."}
    assert passe1._ler_skill_ativa() == "revisor"
    # proxima sessao recarrega sozinha
    entradas2 = iter(["/sair"])
    assert passe1._repl("https://x", CHAVE, "m", lambda prompt: next(entradas2)) == 0
    assert "Skill ativa: revisor." in capsys.readouterr().out


def test_repl_skill_off_e_limpar(capsys):
    _logar_direto()
    corpos = []

    def handler(request):
        corpos.append(json.loads(request.content.decode()))
        return _resposta_chat("eco!")

    _mockar(handler)
    conf = Path(os.environ["PASSE1_CONFIG_DIR"])
    _criar_skill(conf / "skills", "revisor")
    entradas = iter(["/skill revisor", "/limpar", "oi", "/skill off", "oi2", "/sair"])
    assert passe1._repl("https://x", CHAVE, "m", lambda prompt: next(entradas)) == 0
    saida = capsys.readouterr().out
    assert "Skill desativada." in saida
    assert corpos[0]["messages"][0]["role"] == "system"
    # /skill off volta ao padrao (sem skill, mas com pt-BR), nao some o system
    assert corpos[1]["messages"][0] == {
        "role": "system",
        "content": passe1.SISTEMA_PADRAO,
    }
    assert corpos[1]["messages"][-1] == {"role": "user", "content": "oi2"}
    assert passe1._ler_skill_ativa() is None


def test_repl_skill_salva_sumiu_segue_sem_skill(capsys):
    _logar_direto()
    passe1._guardar_skill_ativa("fantasma")
    entradas = iter(["/sair"])
    assert passe1._repl("https://x", CHAVE, "m", lambda prompt: next(entradas)) == 0
    assert "sumiu; seguindo sem skill" in capsys.readouterr().out
    assert passe1._ler_skill_ativa() is None
