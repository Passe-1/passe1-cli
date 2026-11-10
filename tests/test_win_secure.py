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
    """Verifica substituição relativa ao diretório com alvo aberto."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll = ctypes.WinDLL('ntdll')
    class IOStatus(ctypes.Structure):
        _fields_ = [('Status', ctypes.c_void_p), ('Information', ctypes.c_size_t)]
    ntdll.NtSetInformationFile.argtypes = [wintypes.HANDLE, ctypes.POINTER(IOStatus), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG]
    ntdll.NtSetInformationFile.restype = ctypes.c_long
    destino = tmp_path / 'original.txt'
    destino.write_text('antes', encoding='utf-8')
    temporario = tmp_path / 'temporario.txt'
    temporario.write_text('depois', encoding='utf-8')
    invalido = wintypes.HANDLE(-1).value
    diretorio = kernel.CreateFileW(str(tmp_path), 0x100081, 1, None, 3, 0x02000000, None)
    original = kernel.CreateFileW(str(destino), 0xC0000000, 7, None, 3, 0, None)
    novo = kernel.CreateFileW(str(temporario), 0x10000, 7, None, 3, 0, None)
    try:
        assert all(h != invalido for h in (diretorio, original, novo))
        class Overlapped(ctypes.Structure):
            _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t), ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]
        kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
        bloqueio = Overlapped()
        assert kernel.LockFileEx(original, 3, 0, 0xffffffff, 0xffffffff, ctypes.byref(bloqueio)), f'LockFileEx falhou: {ctypes.get_last_error()}'
        nome = destino.name
        class RenameInfo(ctypes.Structure):
            _fields_ = [('Flags', wintypes.DWORD), ('RootDirectory', wintypes.HANDLE), ('FileNameLength', wintypes.DWORD), ('FileName', wintypes.WCHAR * len(nome))]
        dados = RenameInfo(3, diretorio, len(nome.encode('utf-16-le')), nome)
        status_io = IOStatus()
        status = ntdll.NtSetInformationFile(novo, ctypes.byref(status_io), ctypes.byref(dados), ctypes.sizeof(dados), 65)
        assert status >= 0, f'NtSetInformationFile falhou: 0x{status & 0xffffffff:08x}'
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


def test_recusa_stream_alternativo(tmp_path):
    from passe1_engine.win_secure import substituir
    alvo = tmp_path / 'normal.txt'
    alvo.write_text('original', encoding='utf-8')
    with pytest.raises((OSError, ValueError)):
        substituir(str(alvo) + ':oculto', None, 'x')
    assert alvo.read_text(encoding='utf-8') == 'original'


def test_diretorio_bloqueado_impede_troca_do_alvo(tmp_path):
    """Valida se a trava de diretório impede rename concorrente do alvo."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    alvo = tmp_path / 'alvo.txt'
    alvo.write_text('original', encoding='utf-8')
    invalido = wintypes.HANDLE(-1).value
    diretorio = kernel.CreateFileW(str(tmp_path), 0x100081, 1, None, 3, 0x02000000, None)
    arquivo = kernel.CreateFileW(str(alvo), 0x80000000, 7, None, 3, 0, None)
    try:
        assert diretorio != invalido and arquivo != invalido
        with pytest.raises(OSError):
            os.rename(alvo, tmp_path / 'trocado.txt')
    finally:
        for handle in (arquivo, diretorio):
            if handle != invalido:
                kernel.CloseHandle(handle)


def test_trava_exclusiva_impede_escrita_concorrente(tmp_path):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    class Overlapped(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t), ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]
    kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    alvo = tmp_path / 'alvo.txt'
    alvo.write_text('original', encoding='utf-8')
    invalido = wintypes.HANDLE(-1).value
    primeiro = kernel.CreateFileW(str(alvo), 0xC0000000, 7, None, 3, 0, None)
    segundo = kernel.CreateFileW(str(alvo), 0x40000000, 7, None, 3, 0, None)
    try:
        assert primeiro != invalido and segundo != invalido
        bloqueio = Overlapped()
        assert kernel.LockFileEx(primeiro, 3, 0, 0xffffffff, 0xffffffff, ctypes.byref(bloqueio))
        dados = ctypes.create_string_buffer(b'x')
        escritos = wintypes.DWORD()
        assert not kernel.WriteFile(segundo, dados, 1, ctypes.byref(escritos), None)
    finally:
        for handle in (segundo, primeiro):
            if handle != invalido:
                kernel.CloseHandle(handle)
    assert alvo.read_text(encoding='utf-8') == 'original'
