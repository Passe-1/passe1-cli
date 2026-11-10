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


def test_escolhe_release_anterior_se_a_mais_recente_estiver_incompleta():
    nome = 'passe1-0.2.7-py3-none-any.whl'
    antigas = {'tag_name': 'v0.2.7', 'assets': [asset(nome, 'https://github.com/Passe-1/passe1-cli/releases/download/v0.2.7/' + nome)]}
    recentes = {'tag_name': 'v0.3.0', 'assets': []}
    tag, url = modulo.escolher_release([recentes, antigas], [Tag('py3', 'none', 'any')])
    assert tag == 'v0.2.7' and url.endswith(nome)


def test_spec_explicito_preserva_instalacao_sem_acessar_release(monkeypatch):
    chamadas = []
    monkeypatch.setenv('PASSE1_SPEC', 'passe1==0.2.7')
    monkeypatch.setattr(modulo.shutil, 'which', lambda _: '/bin/pipx')
    monkeypatch.setattr(modulo, 'urlopen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('não deve buscar release')))
    monkeypatch.setattr(modulo.subprocess, 'run', lambda cmd, check: chamadas.append(cmd))
    assert modulo.main() == 0
    assert chamadas == [['/bin/pipx', 'install', '--force', 'passe1==0.2.7']]


@pytest.mark.parametrize('plataforma', [
    'macosx_11_0_arm64', 'macosx_10_13_x86_64', 'linux_x86_64',
    'linux_aarch64', 'win_amd64', 'win_arm64',
])
def test_seleciona_cada_plataforma(plataforma):
    nome = f'passe1-0.3.0-py3-none-{plataforma}.whl'
    release = {'tag_name': 'v0.3.0', 'assets': [asset(nome)]}
    assert modulo.selecionar(release, [Tag('py3', 'none', plataforma)]).endswith(nome)
