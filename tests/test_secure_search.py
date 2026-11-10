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


def test_temporario_tem_permissoes_restritas_e_e_removido(tmp_path, monkeypatch):
    import shutil
    import stat
    import subprocess
    ripgrep = shutil.which('rg')
    if not ripgrep or os.name != 'posix':
        pytest.skip('Validação de permissões POSIX requer ripgrep')
    arquivo = tmp_path / 'publico.txt'
    arquivo.write_text('texto de teste')
    original = subprocess.run
    vistos = []
    def verificar(*args, **kwargs):
        handle = kwargs['stdin']
        diretorio = handle._TemporaryFileArgs['dir']
        assert stat.S_IMODE(os.stat(diretorio).st_mode) == 0o700
        assert stat.S_IMODE(os.fstat(handle.fileno()).st_mode) == 0o600
        vistos.append(diretorio)
        return original(*args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', verificar)
    assert secure_search.buscar_candidatos('texto', [str(arquivo)], ripgrep)
    assert vistos and all(not os.path.exists(pasta) for pasta in vistos)


def test_temporarios_removidos_quando_ripgrep_falha(tmp_path, monkeypatch):
    import subprocess
    import tempfile
    arquivo = tmp_path / 'publico.txt'
    arquivo.write_text('texto de teste')
    original = tempfile.TemporaryDirectory
    criados = []
    def diretorio(*args, **kwargs):
        item = original(*args, **kwargs)
        criados.append(item.name)
        return item
    def falhar(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 2, b'', b'erro')
    monkeypatch.setattr(tempfile, 'TemporaryDirectory', diretorio)
    monkeypatch.setattr(subprocess, 'run', falhar)
    with pytest.raises(ValueError):
        secure_search.buscar_candidatos('texto', [str(arquivo)], 'rg')
    assert criados and all(not os.path.exists(pasta) for pasta in criados)
