import json
import stat
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import passe1


@pytest.fixture(autouse=True)
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("PASSE1_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PASSE1_BASE_URL", "https://gateway.teste/v1")
    monkeypatch.setattr(passe1, "_transporte", None)


def test_login_conta_recupera_chave_existente_e_mostra_saldo(monkeypatch, capsys):
    chave = passe1.PREFIXO_CHAVE + "ficticia"
    requisicoes = []
    def handler(request):
        requisicoes.append((request.method, request.url.path))
        if request.url.path == "/api/cli/iniciar":
            return httpx.Response(200, json={"browser_code": "a" * 43, "device_code": "b" * 43, "expires_in": 600})
        if request.url.path == "/api/cli/token":
            assert json.loads(request.content)["device_code"] == "b" * 43
            return httpx.Response(200, json={"status": "authorized"}, headers={"set-cookie": "passe1_session=token-teste; Path=/; HttpOnly"})
        assert request.headers["cookie"] == "passe1_session=token-teste"
        if request.url.path == "/api/me":
            return httpx.Response(200, json={"usuario": {"email": "a@b.com"}, "credito": {"saldo_cents": 1250, "bonus_cents": 50}, "chaves": [{"id": "abc", "masked": "sk-...1234", "status": "ativa"}]})
        if request.url.path == "/api/keys/reveal":
            return httpx.Response(200, json={"key": chave})
        raise AssertionError(request.url)
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(handler))
    monkeypatch.setattr(passe1.webbrowser, "open", lambda _: True)
    monkeypatch.setattr("builtins.input", lambda _: "1")
    assert passe1.main(["login"]) == 0
    assert passe1._ler_chave() == chave
    assert stat.S_IMODE((passe1._dir_config() / "session").stat().st_mode) == 0o600
    saida = capsys.readouterr().out
    assert "R$ 12,50" in saida
    assert "Código de confirmação: aaaaaaaa" in saida
    assert "Não autorize se for diferente" in saida
    assert ("POST", "/api/keys") not in requisicoes


def test_login_conta_sem_chave_mostra_recarga_e_nao_cria(monkeypatch, capsys):
    def handler(request):
        if request.url.path == "/api/cli/iniciar":
            return httpx.Response(200, json={"browser_code": "a" * 43, "device_code": "b" * 43, "expires_in": 600})
        if request.url.path == "/api/cli/token":
            return httpx.Response(200, json={"status": "authorized"}, headers={"set-cookie": "passe1_session=token; Path=/"})
        if request.url.path == "/api/me":
            return httpx.Response(200, json={"usuario": {"email": "x@y.com"}, "credito": {"saldo_cents": 0, "bonus_cents": 0, "pode_criar_chave": False}, "chaves": []})
        raise AssertionError(request.url)
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(handler))
    monkeypatch.setattr(passe1.webbrowser, "open", lambda _: True)
    assert passe1.main(["login"]) == 0
    assert "recarreg" in capsys.readouterr().out.lower()
    assert passe1._ler_chave() is None


def test_status_conta_mostra_saldo_e_bonus(monkeypatch, capsys):
    passe1._guardar_sessao("https://gateway.teste", "passe1_session", "token")
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(lambda req: httpx.Response(200, json={"saldo_centavos": 2500, "bonus_centavos": 75, "moeda": "BRL"})))
    assert passe1.main(["status"]) == 0
    saida = capsys.readouterr().out
    assert "R$ 25,00" in saida and "R$ 0,75" in saida


def test_login_senha_recusa_http_remoto(monkeypatch, capsys):
    monkeypatch.setenv("PASSE1_BASE_URL", "http://gateway.teste/v1")
    monkeypatch.setattr(passe1.getpass, "getpass", lambda _: pytest.fail("senha solicitada em HTTP"))
    assert passe1.main(["login"]) == 1
    assert "HTTPS" in capsys.readouterr().out


def test_config_modelo_persiste_e_ambiente_tem_precedencia(monkeypatch, capsys):
    assert passe1.main(["config", "model", "modelo-a"]) == 0
    assert passe1._modelo_padrao() == "modelo-a"
    monkeypatch.setenv("PASSE1_MODEL", "modelo-b")
    assert passe1._modelo_padrao() == "modelo-b"


def test_chave_da_conta_nao_e_usada_em_outro_gateway(monkeypatch):
    passe1._guardar_sessao("https://gateway.teste", "passe1_session", "token")
    passe1._guardar_chave(passe1.PREFIXO_CHAVE + "ficticia")
    assert passe1._ler_chave("https://outro.teste/v1") is None


def test_troca_de_conta_nao_reutiliza_chave_anterior():
    passe1._guardar_sessao("https://gateway.teste", "passe1_session", "conta-a")
    passe1._guardar_chave(passe1.PREFIXO_CHAVE + "conta-a")
    passe1._guardar_sessao("https://gateway.teste", "passe1_session", "conta-b")
    assert passe1._ler_chave() is None


def test_sessao_corrompida_nao_libera_chave_da_conta():
    passe1._guardar_sessao("https://gateway.teste", "passe1_session", "token")
    passe1._guardar_chave(passe1.PREFIXO_CHAVE + "ficticia")
    (passe1._dir_config() / "session").write_text("inválido")
    assert passe1._ler_chave() is None


def test_criar_chave_explicitamente_guarda_credencial_sem_expor(monkeypatch, capsys):
    passe1._guardar_sessao("https://gateway.teste", "passe1_session", "token")
    chave = passe1.PREFIXO_CHAVE + "segredo-ficticio"
    def handler(request):
        assert request.method == "POST" and request.url.path == "/api/keys"
        return httpx.Response(201, json={"key": chave, "masked": "sk-...icio"})
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(handler))
    assert passe1.main(["keys", "create"]) == 0
    assert passe1._ler_chave() == chave
    assert chave not in capsys.readouterr().out


def test_uso_da_conta_lista_custo_por_modelo(monkeypatch, capsys):
    passe1._guardar_sessao("https://gateway.teste", "passe1_session", "token")
    def handler(request):
        assert request.url.path == "/api/me/uso"
        return httpx.Response(200, json={"por_modelo": [{"modelo": "modelo-a", "requests": 3, "tokens_entrada": 100, "tokens_saida": 20, "custo_centavos": 125}]})
    monkeypatch.setattr(passe1, "_transporte", httpx.MockTransport(handler))
    assert passe1.main(["usage"]) == 0
    saida = capsys.readouterr().out
    assert "modelo-a" in saida and "R$ 1,25" in saida and "100" in saida


def test_http_remoto_nao_recebe_chave_em_qualquer_comando(monkeypatch, capsys):
    chave = passe1.PREFIXO_CHAVE + 'ficticia'
    monkeypatch.setenv('PASSE1_BASE_URL', 'http://gateway.teste/v1')
    monkeypatch.setattr(passe1, '_transporte', httpx.MockTransport(lambda _: pytest.fail('credencial enviada em HTTP')))
    assert passe1.main(['login', chave]) == 1
    assert 'HTTPS' in capsys.readouterr().out
    passe1._guardar_chave(chave)
    assert passe1.main(['chat', 'oi']) == 1
    assert 'HTTPS' in capsys.readouterr().out


def test_http_local_permitido_e_http_remoto_recusado():
    assert passe1._origem('http://127.0.0.1:8091/v1') == 'http://127.0.0.1:8091'
    assert passe1._origem('http://localhost:8091/v1') == 'http://localhost:8091'
    with pytest.raises(ValueError, match='HTTPS'):
        passe1._origem('http://example.org/v1')
