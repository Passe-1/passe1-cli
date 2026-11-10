import os

import pytest

from passe1_engine import secure_search


def test_busca_resultado_de_arquivo_validado(tmp_path):
    (tmp_path / 'a.txt').write_text('uma linha\noutra linha')
    assert secure_search.buscar(tmp_path, 'outra', '*.txt') == [(str(tmp_path / 'a.txt'), 2, 'outra linha')]


def test_busca_ignora_alias_de_segredo(tmp_path):
    segredo = tmp_path / '.env'
    segredo.write_text('SEGREDO_FIXTURE')
    os.link(segredo, tmp_path / 'alias.txt')
    assert secure_search.buscar(tmp_path, 'SEGREDO_FIXTURE') == []


def test_busca_ignora_ancestral_symlink(tmp_path):
    externo = tmp_path / 'externo'
    externo.mkdir()
    (externo / 'a.txt').write_text('SEGREDO_FIXTURE')
    projeto = tmp_path / 'projeto'
    projeto.mkdir()
    try:
        (projeto / 'link').symlink_to(externo, target_is_directory=True)
    except OSError:
        pytest.skip('Criação de symlink indisponível no sistema')
    assert secure_search.buscar(projeto, 'SEGREDO_FIXTURE') == []


def test_troca_de_alias_antes_da_leitura_nao_revela_resultado_antigo(tmp_path, monkeypatch):
    from passe1_engine import secure_stream
    segredo = tmp_path / '.env'
    segredo.write_text('SEGREDO_FIXTURE')
    alias = tmp_path / 'alias.txt'
    os.link(segredo, alias)
    original = secure_stream.transmitir
    def trocar(caminho, destino, *args):
        alias.unlink()
        alias.write_text('conteudo publico')
        return original(caminho, destino, *args)
    monkeypatch.setattr(secure_stream, 'transmitir', trocar)
    assert secure_search.buscar(tmp_path, 'SEGREDO_FIXTURE') == []


def test_ripgrep_processa_somente_conteudo_validado(tmp_path, monkeypatch):
    import shutil

    from passe1_engine import secure_stream
    ripgrep = shutil.which('rg')
    if not ripgrep:
        pytest.skip('ripgrep não disponível')
    segredo = tmp_path / '.env'
    segredo.write_text('SEGREDO_FIXTURE')
    alias = tmp_path / 'alias.txt'
    os.link(segredo, alias)
    original = secure_stream.transmitir
    def trocar(caminho, destino, *args):
        alias.unlink()
        alias.write_text('conteudo publico')
        return original(caminho, destino, *args)
    monkeypatch.setattr(secure_stream, 'transmitir', trocar)
    assert secure_search.buscar_candidatos('SEGREDO_FIXTURE', [str(alias)], ripgrep) == []


def test_busca_sem_copia_temporaria(tmp_path, monkeypatch):
    import shutil
    import tempfile
    ripgrep = shutil.which('rg')
    if not ripgrep:
        pytest.skip('ripgrep não disponível')
    arquivo = tmp_path / 'publico.txt'
    arquivo.write_text('texto de teste')
    def proibido(*args, **kwargs):
        raise AssertionError('A busca não deve criar cópias temporárias')
    monkeypatch.setattr(tempfile, 'TemporaryDirectory', proibido)
    assert secure_search.buscar_candidatos('texto', [str(arquivo)], ripgrep)


def test_descarta_resultados_se_validacao_final_falhar(tmp_path, monkeypatch):
    import shutil

    from passe1_engine import secure_stream
    ripgrep = shutil.which('rg')
    if not ripgrep:
        pytest.skip('ripgrep não disponível')
    arquivo = tmp_path / 'publico.txt'
    arquivo.write_text('texto de teste')
    def leitura_invalida(caminho, destino, *args):
        destino.write(b'SEGREDO_FIXTURE')
        destino.flush()
        raise ValueError('arquivo mudou')
    monkeypatch.setattr(secure_stream, 'transmitir', leitura_invalida)
    assert secure_search.buscar_candidatos('SEGREDO_FIXTURE', [str(arquivo)], ripgrep) == []


def test_regex_invalida_retorna_erro(tmp_path):
    import shutil
    ripgrep = shutil.which('rg')
    if not ripgrep:
        pytest.skip('ripgrep não disponível')
    arquivo = tmp_path / 'publico.txt'
    arquivo.write_text('conteudo')
    with pytest.raises(ValueError, match='expressão'):
        secure_search.buscar_candidatos('[', [str(arquivo)], ripgrep)


def test_regex_invalida_no_fechamento_do_pipe_retorna_erro(tmp_path, monkeypatch):
    import io
    import subprocess
    arquivo = tmp_path / 'publico.txt'
    arquivo.write_text('conteudo')
    class Entrada(io.BytesIO):
        def close(self):
            super().close()
            raise BrokenPipeError('ripgrep encerrou')
    class Processo:
        def __init__(self, *args, **kwargs):
            self.stdin = Entrada()
            self.stdout = io.BytesIO()
        def wait(self):
            return 2
        def poll(self):
            return 2
    monkeypatch.setattr(subprocess, 'Popen', Processo)
    with pytest.raises(ValueError, match='expressão'):
        secure_search.buscar_candidatos('[', [str(arquivo)], 'rg')


def test_linha_gigante_retorna_erro_sem_expor_registro(tmp_path):
    import shutil
    ripgrep = shutil.which('rg')
    if not ripgrep:
        pytest.skip('ripgrep não disponível')
    arquivo = tmp_path / 'gigante.txt'
    arquivo.write_text('MATCH' + 'x' * 2_000_000)
    with pytest.raises(ValueError, match='excessivo'):
        secure_search.buscar_candidatos('MATCH', [str(arquivo)], ripgrep)


def test_resultado_de_linha_longa_e_limitado(tmp_path):
    import shutil
    ripgrep = shutil.which('rg')
    if not ripgrep:
        pytest.skip('ripgrep não disponível')
    arquivo = tmp_path / 'longo.txt'
    arquivo.write_text('MATCH' + 'x' * 5000)
    resultado = secure_search.buscar_candidatos('MATCH', [str(arquivo)], ripgrep)
    assert len(resultado[0][2]) == 2000
