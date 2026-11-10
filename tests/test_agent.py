import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import passe1


def test_agente_le_edita_e_devolve_resultado_ao_modelo(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(passe1, "_ler_chave", lambda *args: "chave")
    monkeypatch.setattr("builtins.input", lambda _: "s")
    (tmp_path / "a.txt").write_text("antes", encoding="utf-8")
    respostas = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {"name": "ler_arquivo", "arguments": '{"caminho":"a.txt"}'}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "2", "type": "function", "function": {"name": "editar_arquivo", "arguments": '{"caminho":"a.txt","antigo":"antes","novo":"depois"}'}}]},
        {"role": "assistant", "content": "Concluído."},
    ]
    recebidas = []

    def handler(request):
        recebidas.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": respostas.pop(0)}]})

    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(handler))
    assert passe1.main(["agent", "altere o arquivo"]) == 0
    assert (tmp_path / "a.txt").read_text() == "depois"
    assert recebidas[1]["messages"][-1]["content"] == "antes"
    assert recebidas[1]["messages"][:2] == recebidas[0]["messages"]
    assert recebidas[2]["messages"][:len(recebidas[1]["messages"])] == recebidas[1]["messages"]
    assert recebidas[0]["tools"] == recebidas[1]["tools"] == recebidas[2]["tools"]
    assert recebidas[2]["messages"][-1]["role"] == "tool"
    assert "Concluído" in capsys.readouterr().out


def test_agente_recusa_caminho_fora_do_projeto(tmp_path):
    import agente
    resultado = agente.executar_ferramenta(tmp_path, "ler_arquivo", {"caminho": "../segredo"}, lambda _: "s")
    assert "fora do projeto" in resultado


def test_agente_nao_edita_sem_aprovacao(tmp_path):
    import agente
    arquivo = tmp_path / "a.txt"
    arquivo.write_text("antes")
    resultado = agente.executar_ferramenta(tmp_path, "editar_arquivo", {"caminho": "a.txt", "antigo": "antes", "novo": "depois"}, lambda _: "n")
    assert arquivo.read_text() == "antes"
    assert "recusada" in resultado


def test_agente_executa_comando_aprovado_no_projeto(tmp_path):
    import agente
    resultado = agente.executar_ferramenta(tmp_path, "terminal", {"comando": "pwd"}, lambda _: "s")
    assert "Código de saída: 0" in resultado
    assert str(tmp_path.resolve()) in resultado


def test_agente_nao_executa_comando_recusado(tmp_path):
    import agente
    resultado = agente.executar_ferramenta(tmp_path, "terminal", {"comando": "touch criado"}, lambda _: "n")
    assert "recusada" in resultado
    assert not (tmp_path / "criado").exists()


def test_agente_recusa_symlink_para_fora_do_projeto(tmp_path):
    import agente
    (tmp_path / "fora").symlink_to(tmp_path.parent, target_is_directory=True)
    resultado = agente.executar_ferramenta(tmp_path, "ler_arquivo", {"caminho": "fora/segredo"}, lambda _: "s")
    assert "fora do projeto" in resultado


def test_agente_interativo_mantem_contexto_entre_tarefas(tmp_path, monkeypatch, capsys):
    import agente
    monkeypatch.chdir(tmp_path)
    recebidas = []
    sessoes = []
    def handler(request):
        recebidas.append(json.loads(request.content))
        sessoes.append(request.headers.get("x-conversation-id"))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Feito"}}], "usage": {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 80}}})
    entradas = iter(["primeira tarefa", "segunda tarefa", "/sair"])
    cliente = lambda base, timeout: httpx.Client(base_url=base, transport=httpx.MockTransport(handler))
    sucesso, erro = agente.executar("https://gateway.teste/v1", "chave", "modelo", "", cliente, lambda _: next(entradas), interativo=True)
    assert sucesso and not erro
    assert recebidas[1]["messages"][1:] == [
        {"role": "user", "content": "primeira tarefa"},
        {"role": "assistant", "content": "Feito"},
        {"role": "user", "content": "segunda tarefa"},
    ]
    assert "80" in capsys.readouterr().out
    assert sessoes[0] and sessoes[0] == sessoes[1]


def test_limpar_contexto_troca_id_de_conversa(tmp_path, monkeypatch):
    import agente
    monkeypatch.chdir(tmp_path)
    ids = []
    def handler(request):
        ids.append(request.headers["x-conversation-id"])
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
    entradas = iter(["primeira", "/limpar", "segunda", "/sair"])
    cliente = lambda base, timeout: httpx.Client(base_url=base, transport=httpx.MockTransport(handler))
    sucesso, _ = agente.executar("https://gateway.teste/v1", "chave", "modelo", "", cliente, lambda _: next(entradas), interativo=True)
    assert sucesso and len(ids) == 2 and ids[0] != ids[1]


def test_busca_encontra_linhas_e_ignora_git(tmp_path):
    import agente
    (tmp_path / "codigo.py").write_text("inicio\ntermo aqui\nfim\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("termo secreto")
    resultado = agente.executar_ferramenta(tmp_path, "buscar", {"texto": "termo"}, lambda _: "n")
    assert "codigo.py:2:termo aqui" in resultado
    assert ".git" not in resultado


def test_agente_respeita_instrucoes_do_projeto(tmp_path, monkeypatch):
    import agente
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_text("Execute pytest antes de concluir.")
    pedidos = []
    def handler(request):
        pedidos.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
    cliente = lambda base, timeout: httpx.Client(base_url=base, transport=httpx.MockTransport(handler))
    sucesso, _ = agente.executar("https://gateway.teste/v1", "chave", "modelo", "tarefa", cliente)
    assert sucesso
    assert any("Execute pytest antes de concluir." in m["content"] for m in pedidos[0]["messages"] if isinstance(m.get("content"), str))


def test_arquivo_sensivel_exige_confirmacao_antes_da_leitura(tmp_path):
    import agente
    (tmp_path / '.env').write_text('TOKEN=valor-privado')
    recusado = agente.executar_ferramenta(tmp_path, 'ler_arquivo', {'caminho': '.env'}, lambda _: 'n')
    assert 'recusada' in recusado and 'valor-privado' not in recusado
    aceito = agente.executar_ferramenta(tmp_path, 'ler_arquivo', {'caminho': '.env'}, lambda _: 's')
    assert 'valor-privado' in aceito


def test_edicao_de_arquivo_sensivel_nao_le_antes_de_confirmar(tmp_path, capsys):
    import agente
    (tmp_path / 'server.pem').write_text('material-privado')
    resultado = agente.executar_ferramenta(tmp_path, 'editar_arquivo', {'caminho': 'server.pem', 'antigo': 'material-privado', 'novo': 'novo'}, lambda _: 'n')
    assert 'recusada' in resultado and 'material-privado' not in capsys.readouterr().out
    assert (tmp_path / 'server.pem').read_text() == 'material-privado'


def test_busca_ignora_arquivos_sensiveis_nao_ocultos(tmp_path):
    import agente
    (tmp_path / 'credentials.json').write_text('busca segredo')
    (tmp_path / 'server.key').write_text('busca segredo')
    (tmp_path / 'normal.txt').write_text('busca pública')
    resultado = agente.executar_ferramenta(tmp_path, 'buscar', {'texto': 'busca'}, lambda _: 'n')
    assert 'normal.txt' in resultado and 'segredo' not in resultado


def test_nome_sensivel_de_link_simbolico_exige_confirmacao(tmp_path):
    import agente
    (tmp_path / 'dados.txt').write_text('segredo')
    (tmp_path / '.env').symlink_to(tmp_path / 'dados.txt')
    resultado = agente.executar_ferramenta(tmp_path, 'ler_arquivo', {'caminho': '.env'}, lambda _: 'n')
    assert 'recusada' in resultado and 'segredo' not in resultado


def test_outros_nomes_sensiveis_exigem_aprovacao(tmp_path):
    import agente
    for nome in (".envrc", "id_ecdsa", "service-account.json"):
        (tmp_path / nome).write_text("segredo")
        resultado = agente.executar_ferramenta(tmp_path, "ler_arquivo", {"caminho": nome}, lambda _: "n")
        assert "recusada" in resultado and "segredo" not in resultado
