import os
from pathlib import Path

import pytest

import arquivos_seguros
import passe1
from passe1_engine import secure_search


def test_backup_reservado_nao_pode_ser_enviado_como_imagem(tmp_path):
    backup = tmp_path / ('.passe1-move-' + 'a' * 32 + '-origem')
    backup.write_bytes(b'\x89PNG\r\n\x1a\nsegredo')
    with pytest.raises(ValueError, match='não podem ser anexados'):
        passe1._imagens([str(backup)])


@pytest.mark.skipif(os.name != 'posix', reason='API ancorada POSIX')
def test_backup_reservado_nao_pode_ser_lido_ou_buscado(tmp_path):
    backup = tmp_path / ('.passe1-move-' + 'a' * 32 + '-origem')
    backup.write_text('SEGREDO_QUARENTENA')
    with pytest.raises(ValueError, match='Quarentena'):
        arquivos_seguros.ler(Path(tmp_path), backup)
    assert secure_search.buscar(tmp_path, 'SEGREDO_QUARENTENA') == []


def test_ripgrep_nao_le_backup_reservado(tmp_path):
    import shutil
    ripgrep = shutil.which('rg')
    if not ripgrep:
        pytest.skip('ripgrep não disponível')
    backup = tmp_path / ('.passe1-move-' + 'b' * 32 + '-origem')
    backup.write_text('SEGREDO_QUARENTENA')
    assert secure_search.buscar_candidatos('SEGREDO_QUARENTENA', [str(backup)], ripgrep) == []


@pytest.mark.skipif(os.name != 'posix', reason='API ancorada POSIX')
def test_anexo_nao_aceita_alias_de_backup(tmp_path):
    backup = tmp_path / ('.passe1-move-' + 'c' * 32 + '-origem')
    backup.write_bytes(b'\x89PNG\r\n\x1a\nsegredo')
    alias = tmp_path / 'alias.png'
    os.link(backup, alias)
    with pytest.raises(ValueError, match='links'):
        passe1._imagens([str(alias)])
    alias.unlink()
    alias.symlink_to(backup)
    with pytest.raises((ValueError, OSError)):
        passe1._imagens([str(alias)])


@pytest.mark.skipif(os.name != 'posix' or __import__('sys').platform != 'darwin', reason='alias de sistema do macOS')
def test_anexo_em_alias_var_do_macos_continua_funcionando():
    import tempfile
    with tempfile.TemporaryDirectory(dir='/var/tmp') as pasta:
        imagem = Path(pasta) / 'imagem.png'
        imagem.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        assert len(passe1._imagens([str(imagem)])) == 1
