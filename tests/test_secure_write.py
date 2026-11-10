import os

import pytest

from passe1_engine import secure_write

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='backend seguro Windows ainda não implementado')


def test_substitui_somente_conteudo_aprovado(tmp_path):
    arquivo = tmp_path / 'arquivo'
    arquivo.write_text('antes')
    secure_write.substituir(arquivo, 'antes', 'depois')
    assert arquivo.read_text() == 'depois'
    with pytest.raises(ValueError):
        secure_write.substituir(arquivo, 'antes', 'outro')


def test_nao_segue_symlink_ancestral(tmp_path):
    fora = tmp_path / 'fora'
    fora.mkdir()
    alvo = fora / 'alvo'
    alvo.write_text('segredo')
    projeto = tmp_path / 'projeto'
    projeto.symlink_to(fora, target_is_directory=True)
    with pytest.raises(OSError):
        secure_write.substituir(projeto / 'alvo', 'segredo', 'mudou')
    assert alvo.read_text() == 'segredo'


def test_recusa_hardlink(tmp_path):
    alvo = tmp_path / 'alvo'
    alvo.write_text('segredo')
    alias = tmp_path / 'alias'
    os.link(alvo, alias)
    with pytest.raises(ValueError):
        secure_write.substituir(alias, 'segredo', 'mudou')
    assert alvo.read_text() == 'segredo'


def test_troca_de_ancestral_antes_de_publicar_nao_altera_externo(tmp_path, monkeypatch):
    import arquivos_seguros
    projeto = tmp_path / 'projeto'
    projeto.mkdir()
    (projeto / 'alvo').write_text('antes')
    fora = tmp_path / 'fora'
    fora.mkdir()
    externo = fora / 'alvo'
    externo.write_text('externo')
    original = arquivos_seguros._copiar_metadados
    def trocar(origem, destino):
        original(origem, destino)
        projeto.rename(tmp_path / 'projeto-original')
        projeto.symlink_to(fora, target_is_directory=True)
    monkeypatch.setattr(arquivos_seguros, '_copiar_metadados', trocar)
    with pytest.raises(OSError):
        secure_write.substituir(projeto / 'alvo', 'antes', 'mudou')
    assert externo.read_text() == 'externo'


def test_remocao_nao_segue_ancestral_trocado(tmp_path):
    fora = tmp_path / 'fora'
    fora.mkdir()
    arquivo = fora / 'alvo'
    arquivo.write_text('segredo')
    projeto = tmp_path / 'projeto'
    projeto.symlink_to(fora, target_is_directory=True)
    with pytest.raises(OSError):
        secure_write.remover(projeto / 'alvo', 'segredo')
    assert arquivo.read_text() == 'segredo'
