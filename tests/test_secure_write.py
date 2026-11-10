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


def test_recusa_substituicao_por_outro_inode_com_mesmo_conteudo(tmp_path):
    import io
    from passe1_engine import secure_stream
    arquivo = tmp_path / 'arquivo'
    arquivo.write_text('aprovado')
    destino = io.BytesIO()
    identidade = secure_stream.transmitir(arquivo, destino)
    arquivo.rename(tmp_path / 'original')
    arquivo.write_text('aprovado')
    with pytest.raises(ValueError, match='identidade'):
        secure_write.substituir(arquivo, 'aprovado', 'alterado', identidade)
    assert arquivo.read_text() == 'aprovado'


def test_criacao_recusa_troca_de_diretorio_apos_aprovacao(tmp_path):
    projeto = tmp_path / 'projeto'
    projeto.mkdir()
    alvo = projeto / 'novo'
    identidade = secure_write.preparar_criacao(alvo)
    projeto.rename(tmp_path / 'original')
    projeto.mkdir()
    with pytest.raises(ValueError, match='Diretório mudou'):
        secure_write.substituir(alvo, None, 'dados', identidade)
    assert not alvo.exists()


def test_criacao_recusa_diretorio_que_apareceu_depois_da_aprovacao(tmp_path):
    alvo = tmp_path / 'ausente' / 'novo'
    identidade = secure_write.preparar_criacao(alvo)
    alvo.parent.mkdir()
    with pytest.raises(ValueError, match='apareceu'):
        secure_write.substituir(alvo, None, 'dados', identidade)
    assert not alvo.exists()


def test_criacao_permite_criar_diretorios_que_permaneceram_ausentes(tmp_path):
    alvo = tmp_path / 'novo' / 'sub' / 'arquivo'
    identidade = secure_write.preparar_criacao(alvo)
    secure_write.substituir(alvo, None, 'dados', identidade)
    assert alvo.read_text() == 'dados'


def test_diretorio_criado_no_lote_nao_pode_ser_trocado(tmp_path):
    primeiro = tmp_path / 'novo' / 'um'
    segundo = tmp_path / 'novo' / 'dois'
    identidade_original = secure_write.preparar_criacao(segundo)
    criado = secure_write.substituir(primeiro, None, 'um', secure_write.preparar_criacao(primeiro))
    identidade_estendida = {'pais': criado['pais']}
    assert len(identidade_estendida['pais']) > len(identidade_original['pais'])
    primeiro.parent.rename(tmp_path / 'original')
    primeiro.parent.mkdir()
    with pytest.raises(ValueError, match='Diretório mudou'):
        secure_write.substituir(segundo, None, 'dois', identidade_estendida)
    assert not segundo.exists()
