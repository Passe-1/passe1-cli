"""Sondas de hipóteses NTFS, executadas apenas sob demanda; algumas falham por desenho."""
import os

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requer Windows")


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
    diretorio = kernel.CreateFileW(str(tmp_path), 0x100081, 3, None, 3, 0x02000000, None)
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
    diretorio = kernel.CreateFileW(str(tmp_path), 0x100081, 3, None, 3, 0x02000000, None)
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


def test_oplock_sinaliza_troca_antes_de_permitir_rename(tmp_path):
    """Oplock retém uma tentativa concorrente até o handle ser fechado."""
    import ctypes
    import threading
    from ctypes import wintypes
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateEventW.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    class Overlapped(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t), ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]
    class Request(ctypes.Structure):
        _fields_ = [('version', wintypes.WORD), ('length', wintypes.WORD), ('level', wintypes.DWORD), ('flags', wintypes.DWORD)]
    class Response(ctypes.Structure):
        _fields_ = [('version', wintypes.WORD), ('length', wintypes.WORD), ('original', wintypes.DWORD), ('new', wintypes.DWORD), ('flags', wintypes.DWORD), ('access', wintypes.DWORD), ('share', wintypes.WORD)]
    k.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(Overlapped)]
    alvo = tmp_path / 'oplock.txt'
    destino = tmp_path / 'mudou.txt'
    alvo.write_text('original', encoding='utf-8')
    evento = k.CreateEventW(None, True, False, None)
    arquivo = k.CreateFileW(str(alvo), 0x80000000, 7, None, 3, 0x40040000, None)
    invalido = wintypes.HANDLE(-1).value
    assert evento and arquivo != invalido
    tentativa = None
    try:
        entrada = Request(1, ctypes.sizeof(Request), 7, 1)
        saida = Response()
        io = Overlapped()
        io.hEvent = evento
        resultado = k.DeviceIoControl(arquivo, 0x90240, ctypes.byref(entrada), ctypes.sizeof(entrada), ctypes.byref(saida), ctypes.sizeof(saida), None, ctypes.byref(io))
        assert not resultado and ctypes.get_last_error() == 997, f'oplock não concedido: {ctypes.get_last_error()}'
        estado = []
        def trocar():
            try:
                os.rename(alvo, destino)
                estado.append('renomeou')
            except OSError:
                estado.append('recusou')
        tentativa = threading.Thread(target=trocar, daemon=True)
        tentativa.start()
        assert k.WaitForSingleObject(evento, 3000) == 0, 'tentativa não notificou quebra do oplock'
        assert alvo.exists() and not destino.exists()
    finally:
        k.CloseHandle(arquivo)
        if tentativa is not None:
            tentativa.join(timeout=5)
        k.CloseHandle(evento)
    assert tentativa is not None and not tentativa.is_alive()


def test_rename_proprio_nao_quebra_oplock(tmp_path):
    """Determina se publicação própria mantém oplock até concluir."""
    import ctypes
    import threading
    from ctypes import wintypes
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    ntdll = ctypes.WinDLL('ntdll')
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateEventW.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    class Overlapped(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t), ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]
    class Request(ctypes.Structure):
        _fields_ = [('version', wintypes.WORD), ('length', wintypes.WORD), ('level', wintypes.DWORD), ('flags', wintypes.DWORD)]
    class Response(ctypes.Structure):
        _fields_ = [('version', wintypes.WORD), ('length', wintypes.WORD), ('original', wintypes.DWORD), ('new', wintypes.DWORD), ('flags', wintypes.DWORD), ('access', wintypes.DWORD), ('share', wintypes.WORD)]
    class IOStatus(ctypes.Structure):
        _fields_ = [('Status', ctypes.c_void_p), ('Information', ctypes.c_size_t)]
    k.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(Overlapped)]
    ntdll.NtSetInformationFile.argtypes = [wintypes.HANDLE, ctypes.POINTER(IOStatus), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG]
    ntdll.NtSetInformationFile.restype = ctypes.c_long
    alvo, temp = tmp_path / 'alvo.txt', tmp_path / 'temp.txt'
    alvo.write_text('antes', encoding='utf-8')
    temp.write_text('depois', encoding='utf-8')
    evento = k.CreateEventW(None, True, False, None)
    invalido = wintypes.HANDLE(-1).value
    diretorio = k.CreateFileW(str(tmp_path), 0x100081, 3, None, 3, 0x02000000, None)
    antigo = k.CreateFileW(str(alvo), 0x80000000, 7, None, 3, 0x40040000, None)
    novo = k.CreateFileW(str(temp), 0x10000, 7, None, 3, 0, None)
    assert evento and all(h != invalido for h in (diretorio, antigo, novo))
    thread = None
    estado = []
    try:
        entrada, saida, io = Request(1, ctypes.sizeof(Request), 7, 1), Response(), Overlapped()
        io.hEvent = evento
        assert not k.DeviceIoControl(antigo, 0x90240, ctypes.byref(entrada), ctypes.sizeof(entrada), ctypes.byref(saida), ctypes.sizeof(saida), None, ctypes.byref(io)) and ctypes.get_last_error() == 997
        nome = alvo.name
        class RenameInfo(ctypes.Structure):
            _fields_ = [('Flags', wintypes.DWORD), ('RootDirectory', wintypes.HANDLE), ('FileNameLength', wintypes.DWORD), ('FileName', wintypes.WCHAR * len(nome))]
        dados = RenameInfo(3, diretorio, len(nome.encode('utf-16-le')), nome)
        def publicar():
            status_io = IOStatus()
            estado.append(ntdll.NtSetInformationFile(novo, ctypes.byref(status_io), ctypes.byref(dados), ctypes.sizeof(dados), 65))
        thread = threading.Thread(target=publicar, daemon=True)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive(), 'A substituição própria ficou bloqueada pelo oplock.'
        assert estado and estado[0] >= 0, f'Falha no rename: {estado}'
        assert k.WaitForSingleObject(evento, 0) != 0, 'A substituição própria quebrou o oplock.'
    finally:
        k.CloseHandle(antigo)
        if thread is not None:
            thread.join(timeout=5)
        for handle in (novo, diretorio, evento):
            k.CloseHandle(handle)
    assert alvo.read_text(encoding='utf-8') == 'depois'


def test_ancestral_do_journal_nao_pode_ser_trocado_durante_edicao(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    journal = win_secure._journal_path(tmp_path / 'alvo')
    with win_secure._ancorar_journal(journal.parent, criar=True), pytest.raises(OSError):
        os.rename(journal.parent, journal.parent.with_name('mudou'))
    assert journal.parent.is_dir()


def test_leitura_aceita_editor_aberto_e_impede_escrita_concorrente(tmp_path):
    import ctypes
    from ctypes import wintypes

    from passe1_engine import win_secure
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    alvo = tmp_path / 'aberto.txt'
    alvo.write_text('original', encoding='utf-8')
    editor = kernel.CreateFileW(str(alvo), 0x40000000, 7, None, 3, 0, None)
    assert editor != wintypes.HANDLE(-1).value
    try:
        with win_secure.abrir_leitura(alvo) as (fd, _):
            assert os.read(fd, 100) == b'original'
            dados = ctypes.create_string_buffer(b'x')
            escritos = wintypes.DWORD()
            assert not kernel.WriteFile(editor, dados, 1, ctypes.byref(escritos), None)
    finally:
        kernel.CloseHandle(editor)
    assert alvo.read_text(encoding='utf-8') == 'original'

