import importlib.util
from pathlib import Path

import pytest
from pip._vendor.packaging.tags import Tag

spec = importlib.util.spec_from_file_location('instalador', Path(__file__).parents[1] / 'scripts' / 'install.py')
modulo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modulo)


def asset(nome, url=None):
    return {'name': nome, 'browser_download_url': url or f'https://github.com/Passe-1/passe1-cli/releases/download/v0.3.0/{nome}'}


def test_escolhe_wheel_da_plataforma():
    arm = 'passe1-0.3.0-py3-none-macosx_11_0_arm64.whl'
    intel = 'passe1-0.3.0-py3-none-macosx_10_13_x86_64.whl'
    release = {'tag_name': 'v0.3.0', 'assets': [asset(intel), asset(arm)]}
    url = modulo.selecionar(release, [Tag('py3', 'none', 'macosx_11_0_arm64')])
    assert url.endswith('/' + arm)


def test_recusa_origem_ou_versao_diferentes():
    nome = 'passe1-0.3.0-py3-none-win_amd64.whl'
    release = {'tag_name': 'v0.3.0', 'assets': [asset(nome, 'https://malicioso.example/' + nome), asset('passe1-0.2.7-py3-none-win_amd64.whl')]}
    with pytest.raises(RuntimeError, match='compatível'):
        modulo.selecionar(release, [Tag('py3', 'none', 'win_amd64')])
