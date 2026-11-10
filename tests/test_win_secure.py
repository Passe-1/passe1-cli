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


def test_rename_por_handle_substitui_destino_bloqueado(tmp_path):
    """Verifica se NTFS permite replace atômico mantendo destino bloqueado."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    destino = tmp_path / 'original.txt'
    destino.write_text('antes', encoding='utf-8')
    temporario = tmp_path / 'temporario.txt'
    temporario.write_text('depois', encoding='utf-8')
    invalido = wintypes.HANDLE(-1).value
    diretorio = kernel.CreateFileW(str(tmp_path), 0x100081, 3, None, 3, 0x02000000, None)
    original = kernel.CreateFileW(str(destino), 0x80000000, 0, None, 3, 0, None)
    novo = kernel.CreateFileW(str(temporario), 0x10000, 7, None, 3, 0, None)
    try:
        assert all(h != invalido for h in (diretorio, original, novo))
        nome = destino.name
        class RenameInfo(ctypes.Structure):
            _fields_ = [('Flags', wintypes.DWORD), ('RootDirectory', wintypes.HANDLE), ('FileNameLength', wintypes.DWORD), ('FileName', wintypes.WCHAR * len(nome))]
        dados = RenameInfo(3, diretorio, len(nome.encode('utf-16-le')), nome)
        ok = kernel.SetFileInformationByHandle(novo, 22, ctypes.byref(dados), ctypes.sizeof(dados))
        assert ok, f'FileRenameInfoEx falhou: {ctypes.get_last_error()}'
    finally:
        for handle in (novo, original, diretorio):
            if handle != invalido:
                kernel.CloseHandle(handle)
    assert destino.read_text(encoding='utf-8') == 'depois'


def test_criacao_falha_remove_arquivo_incompleto(tmp_path):
    from passe1_engine.win_secure import substituir
    class ConteudoInvalido:
        def encode(self, _):
            raise RuntimeError('falha simulada')
    alvo = tmp_path / 'incompleto.txt'
    with pytest.raises(RuntimeError, match='falha simulada'):
        substituir(alvo, None, ConteudoInvalido())
    assert not alvo.exists()
