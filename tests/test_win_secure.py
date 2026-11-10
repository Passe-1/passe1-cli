import os

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='requer Windows')


def test_criar_sem_substituir(tmp_path):
    from passe1_engine.win_secure import substituir
    destino = tmp_path / 'novo.txt'
    substituir(destino, None, 'conteúdo')
    assert destino.read_text(encoding='utf-8') == 'conteúdo'
    with pytest.raises((OSError, ValueError)):
        substituir(destino, None, 'sobrescrito')
    assert destino.read_text(encoding='utf-8') == 'conteúdo'


def test_recusa_symlink_ancestral(tmp_path):
    from passe1_engine.win_secure import substituir
    fora = tmp_path / 'fora'
    fora.mkdir()
    dentro = tmp_path / 'projeto'
    dentro.mkdir()
    try:
        (dentro / 'alias').symlink_to(fora, target_is_directory=True)
    except OSError:
        pytest.skip('criação de symlink indisponível no runner')
    with pytest.raises((OSError, ValueError)):
        substituir(dentro / 'alias' / 'novo', None, 'x')
    assert not (fora / 'novo').exists()


def test_remover_so_se_conteudo_igual(tmp_path):
    from passe1_engine.win_secure import remover
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('antes', encoding='utf-8')
    with pytest.raises(ValueError):
        remover(alvo, 'diferente')
    assert alvo.read_text(encoding='utf-8') == 'antes'
    remover(alvo, 'antes')
    assert not alvo.exists()


def test_remover_recusa_hardlink(tmp_path):
    from passe1_engine.win_secure import remover
    alvo = tmp_path / 'real.txt'
    alvo.write_text('antes', encoding='utf-8')
    alias = tmp_path / 'alias.txt'
    os.link(alvo, alias)
    with pytest.raises(ValueError):
        remover(alias, 'antes')
    assert alvo.read_text(encoding='utf-8') == 'antes'
    assert alias.exists()
