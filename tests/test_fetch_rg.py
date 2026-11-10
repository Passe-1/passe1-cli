import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'fetch-rg.py'
spec = importlib.util.spec_from_file_location('fetch_rg', SCRIPT)
fetch_rg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_rg)


def archive():
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as tar:
        for name, data in [('rg', b'binario'), ('LICENSE-MIT', b'mit'), ('UNLICENSE', b'unlicense'), ('COPYING', b'copying')]:
            info = tarfile.TarInfo('ripgrep-15.1.0-test/' + name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def test_seis_plataformas_tem_digest_fixado():
    assert set(fetch_rg.TARGETS) == {'macos-arm64', 'macos-x64', 'linux-arm64', 'linux-x64', 'windows-arm64', 'windows-x64'}
    assert all(len(info[1]) == 64 for info in fetch_rg.TARGETS.values())


def test_hash_incorreto_nao_extrai(tmp_path):
    with pytest.raises(ValueError, match='SHA-256'):
        fetch_rg.instalar(archive(), 'test', '0' * 64, tmp_path, windows=False)
    assert not list(tmp_path.iterdir())


def test_extrai_executavel_e_licencas_autenticadas(tmp_path):
    dados = archive()
    fetch_rg.instalar(dados, 'test', hashlib.sha256(dados).hexdigest(), tmp_path, windows=False)
    assert (tmp_path / 'rg').read_bytes() == b'binario'
    assert (tmp_path / 'ripgrep-LICENSE-MIT').read_bytes() == b'mit'
    assert (tmp_path / 'ripgrep-UNLICENSE').read_bytes() == b'unlicense'
    assert (tmp_path / 'ripgrep-COPYING').read_bytes() == b'copying'
    assert (tmp_path / 'rg').stat().st_mode & 0o111
