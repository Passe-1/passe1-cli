import json
import os
import stat

import httpx
import pytest

import passe1


def test_retomada_envia_contexto_anterior_sem_salvar_chave(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PASSE1_CONFIG_DIR", str(tmp_path / "cfg"))
    passe1._guardar_chave("sk-passe1-segredo-teste")
    requests = []

    def handler(req):
        body = json.loads(req.content)
        requests.append(body["messages"])
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Resposta anterior"}}]})

    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(handler))
    assert passe1.main(["agent", "--session", "trabalho", "Primeira tarefa"]) == 0
    assert passe1.main(["agent", "--session", "trabalho", "Segunda tarefa"]) == 0
    assert [m["content"] for m in requests[1] if m["role"] in {"user", "assistant"}] == ["Primeira tarefa", "Resposta anterior", "Segunda tarefa"]
    arquivo = tmp_path / "cfg" / "sessions" / "trabalho.json"
    assert stat.S_IMODE(arquivo.stat().st_mode) == 0o600
    assert "sk-passe1-segredo-teste" not in arquivo.read_text()


def test_sessao_nao_pode_ser_aberta_em_outro_projeto(tmp_path, monkeypatch):
    monkeypatch.setenv("PASSE1_CONFIG_DIR", str(tmp_path / "cfg"))
    passe1._guardar_chave("sk-passe1-segredo-teste")
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]})))
    primeiro = tmp_path / "a"
    segundo = tmp_path / "b"
    primeiro.mkdir(); segundo.mkdir()
    monkeypatch.chdir(primeiro)
    assert passe1.main(["agent", "--session", "isolada", "oi"]) == 0
    monkeypatch.chdir(segundo)
    assert passe1.main(["agent", "--session", "isolada", "oi"]) == 1


def test_falha_da_segunda_rodada_preserva_ultima_resposta(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PASSE1_CONFIG_DIR", str(tmp_path / "cfg"))
    passe1._guardar_chave("sk-passe1-segredo-teste")
    respostas = [httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]}),
                 httpx.Response(503, text="indisponível")]
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda req: respostas.pop(0)))
    assert passe1.main(["agent", "--session", "trabalho", "concluída"]) == 0
    assert passe1.main(["agent", "--session", "trabalho", "falhou"]) == 1
    dados = json.loads((tmp_path / "cfg" / "sessions" / "trabalho.json").read_text())
    assert [m["content"] for m in dados["mensagens"]] == ["concluída", "OK"]


def test_sessao_recusa_nome_com_caminho(tmp_path):
    from sessao import Sessao
    with pytest.raises(ValueError):
        Sessao(tmp_path, "../fora", tmp_path, "https://site/v1", "modelo", "chave")


def test_sessao_bloqueia_abertura_simultanea(tmp_path):
    from sessao import Sessao
    args = (tmp_path, "trabalho", tmp_path, "https://site/v1", "modelo", "chave")
    with Sessao(*args), pytest.raises(ValueError, match="outro processo"), Sessao(*args):
        pass


def test_apos_ferramenta_e_falha_retomada_recebe_resultado_sem_reexecutar(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PASSE1_CONFIG_DIR", str(tmp_path / "cfg"))
    passe1._guardar_chave("sk-passe1-segredo-teste")
    monkeypatch.setattr("builtins.input", lambda _: "s")
    criacao = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call-1", "type": "function", "function": {
            "name": "criar_arquivo", "arguments": json.dumps({"caminho": "criado.txt", "conteudo": "conteúdo"})}}]}
    requisicoes = []

    def handler(req):
        requisicoes.append(json.loads(req.content)["messages"])
        if len(requisicoes) == 1:
            return httpx.Response(200, json={"choices": [{"message": criacao}]})
        if len(requisicoes) == 2:
            return httpx.Response(503, text="indisponível")
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Concluído"}}]})

    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(handler))
    assert passe1.main(["agent", "--session", "execucao", "Crie o arquivo"]) == 1
    assert (tmp_path / "criado.txt").read_text() == "conteúdo"
    assert passe1.main(["agent", "--session", "execucao", "Continue"]) == 0
    historico = requisicoes[2]
    assert any(m.get("tool_call_id") == "call-1" and m["content"] == "Alteração aplicada." for m in historico)
    assert historico[-1]["content"] == "Continue"
    assert (tmp_path / "criado.txt").read_text() == "conteúdo"


def test_lock_symlink_nao_altera_alvo(tmp_path):
    from sessao import Sessao
    pasta = tmp_path / "sessions"
    pasta.mkdir()
    alvo = tmp_path / "externo"
    alvo.write_text("preservado")
    alvo.chmod(0o644)
    (pasta / "trabalho.lock").symlink_to(alvo)
    with pytest.raises((ValueError, OSError)), Sessao(tmp_path, "trabalho", tmp_path, "https://site/v1", "modelo", "chave"):
        pass
    assert stat.S_IMODE(alvo.stat().st_mode) == 0o644
    assert alvo.read_text() == "preservado"


def test_lote_malformado_nao_executa_ferramentas_anteriores(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PASSE1_CONFIG_DIR", str(tmp_path / "cfg"))
    passe1._guardar_chave("sk-passe1-segredo-teste")
    monkeypatch.setattr("builtins.input", lambda _: "s")
    chamada = {"id": "call-1", "type": "function", "function": {
        "name": "criar_arquivo", "arguments": json.dumps({"caminho": "criado.txt", "conteudo": "x"})}}
    msg = {"role": "assistant", "content": None, "tool_calls": [chamada, {"id": "call-2"}]}
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [{"message": msg}]})))
    assert passe1.main(["agent", "--session", "execucao", "Crie o arquivo"]) == 1
    assert not (tmp_path / "criado.txt").exists()


def test_diretorio_symlink_nao_altera_alvo(tmp_path):
    from sessao import Sessao
    alvo = tmp_path / "externo"
    alvo.mkdir()
    alvo.chmod(0o755)
    (tmp_path / "sessions").symlink_to(alvo, target_is_directory=True)
    with pytest.raises((ValueError, OSError)), Sessao(tmp_path, "trabalho", tmp_path, "https://site/v1", "modelo", "chave"):
        pass
    assert stat.S_IMODE(alvo.stat().st_mode) == 0o755


def test_lock_hardlink_nao_altera_alvo(tmp_path):
    from sessao import Sessao
    pasta = tmp_path / "sessions"
    pasta.mkdir()
    alvo = tmp_path / "externo"
    alvo.write_text("preservado")
    alvo.chmod(0o644)
    os.link(alvo, pasta / "trabalho.lock")
    with pytest.raises((ValueError, OSError)), Sessao(tmp_path, "trabalho", tmp_path, "https://site/v1", "modelo", "chave"):
        pass
    assert stat.S_IMODE(alvo.stat().st_mode) == 0o644
    assert alvo.read_text() == "preservado"


def test_lock_com_permissao_aberta_e_recusado_sem_alterar(tmp_path):
    from sessao import Sessao
    pasta = tmp_path / "sessions"
    pasta.mkdir()
    lock = pasta / "trabalho.lock"
    lock.write_text("")
    lock.chmod(0o644)
    with pytest.raises(ValueError), Sessao(tmp_path, "trabalho", tmp_path, "https://site/v1", "modelo", "chave"):
        pass
    assert stat.S_IMODE(lock.stat().st_mode) == 0o644


def test_interrupcao_no_meio_do_lote_preserva_acoes_anteriores(tmp_path, monkeypatch):
    import agente
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PASSE1_CONFIG_DIR", str(tmp_path / "cfg"))
    passe1._guardar_chave("sk-passe1-segredo-teste")
    chamadas = [
        {"id": "um", "type": "function", "function": {"name": "criar_arquivo", "arguments": json.dumps({"caminho": "criado", "conteudo": "x"})}},
        {"id": "dois", "type": "function", "function": {"name": "terminal", "arguments": json.dumps({"comando": "echo teste"})}},
    ]
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": chamadas}}]})))
    original = agente.executar_ferramenta
    def executar(raiz, nome, args, ler, **kwargs):
        if nome == "terminal":
            raise KeyboardInterrupt()
        return original(raiz, nome, args, lambda _: "s", **kwargs)
    monkeypatch.setattr(agente, "executar_ferramenta", executar)
    with pytest.raises(KeyboardInterrupt):
        passe1.main(["agent", "--session", "lote", "crie e teste"])
    assert (tmp_path / "criado").read_text() == "x"
    dados = json.loads((tmp_path / "cfg" / "sessions" / "lote.json").read_text())
    resultados = {m["tool_call_id"]: m["content"] for m in dados["mensagens"] if m["role"] == "tool"}
    assert resultados["um"] == "Alteração aplicada."
    assert "incerto" in resultados["dois"].lower()
