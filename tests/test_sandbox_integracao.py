import agente
import passe1
import sandbox


def test_comando_aprovado_usa_sandbox_quando_solicitado(tmp_path, monkeypatch):
    chamadas = []
    def executar(comando, raiz, timeout, limite):
        chamadas.append((comando, raiz))
        return 0, "ok", False
    monkeypatch.setattr(sandbox, "executar", executar)
    resultado = agente.executar_ferramenta(tmp_path, "terminal", {"comando": "echo ok"}, lambda _: "s", usar_sandbox=True)
    assert chamadas == [("echo ok", tmp_path)]
    assert "ok" in resultado


def test_opcao_sandbox_disponivel_no_agente():
    args = passe1.montar_parser().parse_args(["agent", "--sandbox", "teste"])
    assert args.sandbox is True


def test_proposta_nao_executa_controles_no_terminal(tmp_path, capsys):
    agente.executar_ferramenta(tmp_path, "terminal", {"comando": "echo seguro\r\x1b[2Koculto"}, lambda _: "n")
    saida = capsys.readouterr().out
    assert "\r" not in saida and "\x1b" not in saida
    assert "\\r" in saida and "\\x1b" in saida


def test_diff_nao_executa_controles_no_terminal(tmp_path, capsys):
    (tmp_path / "x.txt").write_text("antigo", encoding="utf-8")
    agente.executar_ferramenta(tmp_path, "editar_arquivo", {"caminho": "x.txt", "antigo": "antigo", "novo": "bom\x1b[2K"}, lambda _: "n")
    saida = capsys.readouterr().out
    assert "\x1b" not in saida and "\\x1b" in saida


def test_chat_stream_imprime_antes_do_fim(monkeypatch, tmp_path, capsys):
    import httpx
    monkeypatch.setenv("PASSE1_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(passe1, "_ler_chave", lambda *args: "sk-passe1-teste")
    class Fluxo(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"primeiro"}}]}\n\n'
            assert "primeiro" in capsys.readouterr().out
            yield b'data: {"choices":[{"delta":{"content":"segundo"}}]}\n\ndata: [DONE]\n\n'
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda _: httpx.Response(200, stream=Fluxo(), headers={"content-type": "text/event-stream"})))
    assert passe1.main(["chat", "oi"]) == 0


def test_chat_nao_executa_controles_da_resposta(capsys):
    import httpx
    resposta = httpx.Response(200, json={"choices": [{"message": {"content": "abc\x1b[2K"}}]})
    texto, erro = passe1._mostrar_resposta_chat(resposta)
    assert erro is None and texto == "abc\x1b[2K"
    saida = capsys.readouterr().out
    assert "\x1b" not in saida and "\\x1b" in saida


def test_chat_falha_de_rede_durante_stream_e_tratada(capsys):
    import httpx
    class Fluxo(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"parte"}}]}\n\n'
            raise httpx.ReadError("conexão encerrada")
    resposta = httpx.Response(200, stream=Fluxo(), headers={"content-type": "text/event-stream"})
    texto, erro = passe1._mostrar_resposta_chat(resposta)
    assert texto is None and "conexão" in erro.lower()
    assert resposta.is_closed


def test_comando_normal_usa_captura_limitada(tmp_path, monkeypatch):
    import processos
    chamadas = []
    def executar(comando, raiz, timeout, limite, *, shell):
        chamadas.append((comando, raiz, shell))
        return 0, "ok", False
    monkeypatch.setattr(processos, "executar", executar)
    resultado = agente.executar_ferramenta(tmp_path, "terminal", {"comando": "echo ok"}, lambda _: "s")
    assert chamadas == [("echo ok", tmp_path, True)]
    assert "ok" in resultado


def test_resposta_do_agente_nao_executa_controles(monkeypatch, tmp_path, capsys):
    import httpx
    monkeypatch.chdir(tmp_path)
    def handler(_):
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "abc\x1b[2K"}}]})
    cliente = lambda base, timeout: httpx.Client(base_url=base, transport=httpx.MockTransport(handler))
    sucesso, erro = agente.executar("https://teste/v1", "chave", "modelo", "tarefa", cliente)
    assert sucesso and not erro
    saida = capsys.readouterr().out
    assert "\x1b" not in saida and "\\x1b" in saida


def test_agente_nao_edita_arquivo_compartilhado_por_hardlink(tmp_path):
    externo = tmp_path.parent / (tmp_path.name + "-externo")
    externo.write_text("original", encoding="utf-8")
    (tmp_path / "link").hardlink_to(externo)
    resultado = agente.executar_ferramenta(tmp_path, "editar_arquivo", {"caminho": "link", "antigo": "original", "novo": "alterado"}, lambda _: "s")
    assert externo.read_text(encoding="utf-8") == "original"
    assert "Erro" in resultado


def test_agente_nao_cria_fora_apos_troca_de_diretorio(tmp_path):
    externo = tmp_path.parent / (tmp_path.name + "-fora")
    externo.mkdir()
    pasta = tmp_path / "pasta"
    pasta.mkdir()
    def aprovar(_):
        pasta.rmdir()
        pasta.symlink_to(externo, target_is_directory=True)
        return "s"
    resultado = agente.executar_ferramenta(tmp_path, "criar_arquivo", {"caminho": "pasta/novo", "conteudo": "x"}, aprovar)
    assert not (externo / "novo").exists()
    assert "Erro" in resultado


def test_agente_nao_le_nem_busca_alias_de_arquivo_sensivel(tmp_path):
    segredo = tmp_path / ".env"
    segredo.write_text("credencial_teste", encoding="utf-8")
    (tmp_path / "notas.txt").hardlink_to(segredo)
    lido = agente.executar_ferramenta(tmp_path, "ler_arquivo", {"caminho": "notas.txt"}, lambda _: "s")
    buscado = agente.executar_ferramenta(tmp_path, "buscar", {"texto": "credencial_teste"}, lambda _: "s")
    assert "credencial_teste" not in lido
    assert "credencial_teste" not in buscado


def test_sandbox_indisponivel_e_recusado_antes_de_chamada_ao_modelo(monkeypatch, capsys):
    monkeypatch.setattr(passe1, "_ler_chave", lambda *args: "sk-passe1-teste")
    monkeypatch.setattr(passe1.sys, "platform", "linux")
    monkeypatch.setattr(agente, "executar", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("não deveria chamar o modelo")))
    assert passe1.main(["agent", "--sandbox", "teste"]) == 1
    assert "indisponível" in capsys.readouterr().out.lower()


def test_stream_interrompido_nao_e_tratado_como_completo():
    import httpx
    resposta = httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"parcial"}}]}\n\n', headers={"content-type": "text/event-stream"})
    texto, erro = passe1._mostrar_resposta_chat(resposta)
    assert texto is None and erro


def test_erro_sse_nao_e_tratado_como_sucesso():
    import httpx
    resposta = httpx.Response(200, content=b'data: {"error":{"message":"falhou"}}\n\n', headers={"content-type": "text/event-stream"})
    texto, erro = passe1._mostrar_resposta_chat(resposta)
    assert texto is None and erro


def test_busca_informa_plataforma_sem_operacoes_seguras(tmp_path, monkeypatch):
    import arquivos_seguros
    def indisponivel():
        raise RuntimeError("Operações seguras indisponíveis")
    monkeypatch.setattr(arquivos_seguros, "_disponivel", indisponivel)
    resultado = agente.executar_ferramenta(tmp_path, "buscar", {"texto": "x"}, lambda _: "n")
    assert "indisponíveis" in resultado


def test_agente_informa_operacoes_de_arquivo_indisponiveis_antes_do_modelo(monkeypatch, capsys):
    import arquivos_seguros
    monkeypatch.setattr(passe1, "_ler_chave", lambda *args: "sk-passe1-teste")
    monkeypatch.setattr(arquivos_seguros, "_disponivel", lambda: (_ for _ in ()).throw(RuntimeError("Operações seguras indisponíveis")))
    monkeypatch.setattr(agente, "executar", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("não deveria chamar o modelo")))
    assert passe1.main(["agent", "teste"]) == 1
    assert "indisponíveis" in capsys.readouterr().out.lower()


def test_stream_malformado_retorna_erro_sem_traceback():
    import httpx
    resposta = httpx.Response(200, content=b'data: {"choices":[null]}\n\ndata: [DONE]\n\n', headers={"content-type": "text/event-stream"})
    texto, erro = passe1._mostrar_resposta_chat(resposta)
    assert texto is None and erro


def test_erro_remoto_no_chat_nao_controla_terminal(monkeypatch, tmp_path, capsys):
    import httpx
    monkeypatch.setenv("PASSE1_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(passe1, "_ler_chave", lambda *args: "sk-passe1-teste")
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda _: httpx.Response(500, json={"error": {"message": "erro\x1b[2K"}})))
    assert passe1.main(["chat", "oi"]) == 1
    saida = capsys.readouterr().out
    assert "\x1b" not in saida and "\\x1b" in saida


def test_erro_remoto_no_agente_nao_controla_terminal(monkeypatch, tmp_path, capsys):
    import httpx
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(passe1, "_ler_chave", lambda *args: "sk-passe1-teste")
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda _: httpx.Response(500, text="falha\x1b[2K")))
    assert passe1.main(["agent", "teste"]) == 1
    saida = capsys.readouterr().out
    assert "\x1b" not in saida and "\\x1b" in saida


def test_login_recusa_codigo_com_caracteres_de_controle(monkeypatch, tmp_path, capsys):
    import httpx
    monkeypatch.setenv("PASSE1_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda _: httpx.Response(200, json={"browser_code": "\x1b" + "a" * 42, "device_code": "b" * 43})))
    monkeypatch.setattr(passe1.webbrowser, "open", lambda _: (_ for _ in ()).throw(AssertionError("não deve abrir navegador")))
    assert passe1.main(["login"]) == 1
    assert "\x1b" not in capsys.readouterr().out


def test_lista_de_chaves_nao_executa_controles(monkeypatch, tmp_path, capsys):
    import httpx
    monkeypatch.setenv("PASSE1_CONFIG_DIR", str(tmp_path))
    passe1._guardar_sessao("https://teste", "passe1_session", "token")
    monkeypatch.setenv("PASSE1_BASE_URL", "https://teste/v1")
    resposta = {"chaves": [{"masked": "sk-...", "status": "ativa", "label": "nome\x1b[2K"}]}
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda _: httpx.Response(200, json=resposta)))
    assert passe1.main(["keys"]) == 0
    saida = capsys.readouterr().out
    assert "\x1b" not in saida and "\\x1b" in saida


def test_stream_cortado_por_limite_nao_e_sucesso():
    import httpx
    resposta = httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"parte"},"finish_reason":"length"}]}\n\ndata: [DONE]\n\n', headers={"content-type": "text/event-stream"})
    texto, erro = passe1._mostrar_resposta_chat(resposta)
    assert texto is None and "limite" in erro.lower()


def test_agente_resposta_malformada_nao_gera_traceback(monkeypatch, tmp_path, capsys):
    import httpx
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(passe1, "_ler_chave", lambda *args: "sk-passe1-teste")
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{"message": None}]})))
    assert passe1.main(["agent", "teste"]) == 1
    assert "falha" in capsys.readouterr().out.lower()


def test_uso_nao_exibe_controle_no_nome_do_modelo(monkeypatch, tmp_path, capsys):
    import httpx
    monkeypatch.setenv("PASSE1_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PASSE1_BASE_URL", "https://teste/v1")
    passe1._guardar_sessao("https://teste", "passe1_session", "token")
    dados = {"por_modelo": [{"modelo": "modelo\x1b[2K", "requests": 1, "tokens_entrada": 1, "tokens_saida": 1, "custo_centavos": 0}]}
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda _: httpx.Response(200, json=dados)))
    assert passe1.main(["usage"]) == 0
    saida = capsys.readouterr().out
    assert "\x1b" not in saida and "\\x1b" in saida


def test_resposta_json_cortada_nao_e_sucesso():
    import httpx
    resposta = httpx.Response(200, json={"choices": [{"message": {"content": "parte"}, "finish_reason": "length"}]})
    texto, erro = passe1._mostrar_resposta_chat(resposta)
    assert texto is None and "limite" in erro.lower()


def test_agente_nao_aceita_resposta_cortada(monkeypatch, tmp_path, capsys):
    import httpx
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(passe1, "_ler_chave", lambda *args: "sk-passe1-teste")
    dados = {"choices": [{"message": {"role": "assistant", "content": "parte"}, "finish_reason": "length"}]}
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda _: httpx.Response(200, json=dados)))
    assert passe1.main(["agent", "teste"]) == 1
    assert "limite" in capsys.readouterr().out.lower()


def test_agente_nao_executa_ferramenta_em_resposta_cortada(monkeypatch, tmp_path):
    import json

    import httpx
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(passe1, "_ler_chave", lambda *args: "sk-passe1-teste")
    tool = {"id": "x", "type": "function", "function": {"name": "criar_arquivo", "arguments": json.dumps({"caminho": "criado", "conteudo": "x"})}}
    dados = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [tool]}, "finish_reason": "length"}]}
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda _: httpx.Response(200, json=dados)))
    assert passe1.main(["agent", "teste"]) == 1
    assert not (tmp_path / "criado").exists()


def test_chat_json_sem_conteudo_nao_e_sucesso():
    import httpx
    resposta = httpx.Response(200, json={"choices": [{"message": {"content": None}, "finish_reason": "stop"}]})
    texto, erro = passe1._mostrar_resposta_chat(resposta)
    assert texto is None and erro


def test_agente_recusa_tool_calls_sem_chamadas(monkeypatch, tmp_path):
    import httpx
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(passe1, "_ler_chave", lambda *args: "sk-passe1-teste")
    dados = {"choices": [{"message": {"role": "assistant", "content": "Criei o arquivo.", "tool_calls": []}, "finish_reason": "tool_calls"}]}
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda _: httpx.Response(200, json=dados)))
    assert passe1.main(["agent", "teste"]) == 1


def test_agente_recusa_stop_com_ferramentas(monkeypatch, tmp_path):
    import json

    import httpx
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(passe1, "_ler_chave", lambda *args: "sk-passe1-teste")
    ferramenta = {"id": "x", "type": "function", "function": {"name": "criar_arquivo", "arguments": json.dumps({"caminho": "criado", "conteudo": "x"})}}
    monkeypatch.setattr(agente, "executar_ferramenta", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("não deve executar ferramenta")))
    dados = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [ferramenta]}, "finish_reason": "stop"}]}
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda _: httpx.Response(200, json=dados)))
    assert passe1.main(["agent", "teste"]) == 1
    assert not (tmp_path / "criado").exists()
