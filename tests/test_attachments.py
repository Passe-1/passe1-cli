import base64
import os
import subprocess
import sys

import pytest

from passe1_engine import attachments as a


def test_texto_imagem_e_bytes_preservados(tmp_path):
    texto = tmp_path / 'com espaço.txt'
    texto.write_text('conteúdo com acentos', encoding='utf-8')
    assert 'conteúdo com acentos' in a.preparar(texto).parte['text']
    imagem = tmp_path / 'imagem.png'
    dados = b'\x89PNG\r\n\x1a\nfixture'
    imagem.write_bytes(dados)
    anexo = a.preparar(imagem)
    imagem.write_bytes(b'alterada')
    assert base64.b64decode(anexo.parte['url'].split(',')[1]) == dados
    assert a.extrair_caminhos(f'"{texto}"') == [str(texto)]
    assert a.extrair_caminhos(texto.as_uri()) == [str(texto)]
    assert a.extrair_caminhos(f'analise {texto}') == []


def test_segredo_exige_consentimento_antes_da_leitura(tmp_path, monkeypatch):
    arquivo = tmp_path / '.env'
    arquivo.write_text('segredo')
    lidos = []
    original = a.transmitir
    monkeypatch.setattr(a, 'transmitir', lambda *args: lidos.append(True))
    with pytest.raises(a.ConsentimentoNecessario):
        a.preparar(arquivo)
    assert not lidos
    monkeypatch.setattr(a, 'transmitir', original)
    assert 'segredo' in a.preparar(arquivo, confirmar=lambda _: True).parte['text']


def test_links_e_limites_sao_recusados(tmp_path):
    original = tmp_path / 'texto'
    original.write_text('normal')
    alias = tmp_path / 'alias'
    os.link(original, alias)
    with pytest.raises(ValueError):
        a.preparar(alias)
    alias.unlink()
    original.unlink()
    original.write_bytes(b'x' * (a.LIMITE_TEXTO + 1))
    with pytest.raises(ValueError, match='256 KiB'):
        a.preparar(original)
    original.write_bytes(b'a\x00b')
    with pytest.raises(ValueError, match='binário'):
        a.preparar(original)


@pytest.mark.skipif(os.name == 'nt', reason='Criação de symlink exige privilégio no Windows.')
def test_link_simbolico_recusado(tmp_path):
    arquivo = tmp_path / 'arquivo'
    arquivo.write_text('normal')
    alias = tmp_path / 'alias'
    alias.symlink_to(arquivo)
    with pytest.raises(ValueError):
        a.preparar(alias)


def test_clipboard_macos_hex(monkeypatch):
    monkeypatch.setattr(a.sys, 'platform', 'darwin')
    monkeypatch.setattr(a.shutil, 'which', lambda _: None)
    dados = b'\x89PNG\r\n\x1a\nfixture'
    saida = ('«data PNGf' + dados.hex() + '»').encode()
    monkeypatch.setattr(a, '_capturar', lambda *args, **kwargs: saida)
    assert a.ler_clipboard().tamanho == len(dados)


def test_clipboard_captura_limitada_e_encerra_produtor(monkeypatch):
    filhos = []
    original = subprocess.Popen

    def criar(*args, **kwargs):
        processo = original(*args, **kwargs)
        filhos.append(processo)
        return processo

    monkeypatch.setattr(a.subprocess, 'Popen', criar)
    with pytest.raises(ValueError, match='4 MiB'):
        a._capturar([sys.executable, '-c', 'import sys,time; sys.stdout.buffer.write(b"x"*65536); sys.stdout.flush(); time.sleep(30)'], limite=1024, timeout=2)
    assert filhos[-1].poll() is not None
    assert a._capturar([sys.executable, '-c', 'import time; time.sleep(30)'], limite=1024, timeout=0.1) is None
    assert filhos[-1].poll() is not None


def test_remotos_recusados_antes_de_consultar_arquivo(monkeypatch):
    consultados = []
    monkeypatch.setattr(a.Path, 'is_file', lambda *_: consultados.append(True))
    for caminho in (r'\\servidor\share\arquivo.txt', '//servidor/share/arquivo.txt', r'\\?\C:\arquivo.txt', 'file://servidor/share/arquivo.txt'):
        assert a.extrair_caminhos(caminho) == []
        with pytest.raises(ValueError, match='remoto|dispositivo'):
            a.preparar(caminho)
    assert not consultados


def test_pendente_nao_le_antes_de_confirmar_e_recusa_substituicao(tmp_path, monkeypatch):
    arquivo = tmp_path / '.env'
    arquivo.write_text('segredo inicial')
    leituras = []
    original = os.read
    monkeypatch.setattr(a.os, 'read', lambda *args: leituras.append(True) or original(*args))
    pendente = a.preparar_pendente(arquivo)
    assert not leituras
    novo = tmp_path / 'novo'
    novo.write_text('segredo trocado')
    novo.replace(arquivo)
    with pytest.raises(ValueError, match='mudou'):
        pendente.confirmar()
    assert not leituras
    pendente.fechar()


def test_pendente_confirma_bytes_do_descritor(tmp_path):
    arquivo = tmp_path / '.env'
    arquivo.write_text('segredo consentido')
    pendente = a.preparar_pendente(arquivo)
    assert 'segredo consentido' in pendente.confirmar().parte['text']
    with pytest.raises(ValueError, match='encerrado'):
        pendente.confirmar()
