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


def test_editar_preserva_conteudo_e_permissoes_basicas(tmp_path):
    from passe1_engine.win_secure import substituir
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('antes', encoding='utf-8')
    antes = alvo.stat()
    substituir(alvo, 'antes', 'depois')
    depois = alvo.stat()
    assert alvo.read_text(encoding='utf-8') == 'depois'
    assert depois.st_mode == antes.st_mode


def test_editar_recusa_versao_divergente(tmp_path):
    from passe1_engine.win_secure import substituir
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('original', encoding='utf-8')
    with pytest.raises(ValueError):
        substituir(alvo, 'versão antiga', 'novo')
    assert alvo.read_text(encoding='utf-8') == 'original'


def test_backup_criptografado_nao_contem_texto_claro(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    journal, identidade = win_secure._guardar_journal(tmp_path / 'alvo.txt', 'segredo de teste', (1, 2, 3))
    try:
        assert b'segredo de teste' not in journal.read_bytes()
        dados = win_secure._ler_journal(journal)
        assert dados['conteudo'] == 'segredo de teste'
        assert dados['identidade'] == [1, 2, 3]
    finally:
        win_secure._apagar_journal(journal, identidade)


def test_recupera_journal_apos_encerramento_durante_escrita(tmp_path, monkeypatch):
    import subprocess
    import sys

    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('conteúdo original', encoding='utf-8')
    programa = '''
import os, sys
from passe1_engine import win_secure as w
original = w._gravar_handle
def interromper(kernel, handle, texto):
    original(kernel, handle, 'parcial')
    os._exit(91)
w._gravar_handle = interromper
w.substituir(sys.argv[1], 'conteúdo original', 'novo')
'''
    resultado = subprocess.run([sys.executable, '-c', programa, str(alvo)], check=False, timeout=20)
    assert resultado.returncode == 91
    assert alvo.read_text(encoding='utf-8') == 'parcial'
    journal = win_secure._journal_path(alvo)
    assert journal.exists()
    assert b'conte' not in journal.read_bytes()
    win_secure.recuperar(alvo)
    assert alvo.read_text(encoding='utf-8') == 'conteúdo original'
    assert not journal.exists()


def test_limpeza_do_journal_nao_apaga_arquivo_substituido(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    journal, identidade = win_secure._guardar_journal(tmp_path / 'alvo.txt', 'segredo', (1, 2, 3))
    anterior = journal.with_name('backup-original')
    journal.rename(anterior)
    journal.write_text('arquivo de terceiro', encoding='utf-8')
    with pytest.raises(ValueError, match='mudou'):
        win_secure._apagar_journal(journal, identidade)
    assert journal.read_text(encoding='utf-8') == 'arquivo de terceiro'
    assert anterior.exists()


def test_recuperacao_recusa_arquivo_substituido(tmp_path, monkeypatch):
    import subprocess
    import sys

    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('conteúdo original', encoding='utf-8')
    programa = '''
import os, sys
from passe1_engine import win_secure as w
original = w._gravar_handle
def interromper(kernel, handle, texto):
    original(kernel, handle, 'parcial')
    os._exit(91)
w._gravar_handle = interromper
w.substituir(sys.argv[1], 'conteúdo original', 'novo')
'''
    resultado = subprocess.run([sys.executable, '-c', programa, str(alvo)], check=False, timeout=20)
    assert resultado.returncode == 91
    novo = tmp_path / 'terceiro.txt'
    novo.write_text('arquivo de terceiro', encoding='utf-8')
    os.replace(novo, alvo)
    with pytest.raises(ValueError, match='não corresponde'):
        win_secure.recuperar(alvo)
    assert alvo.read_text(encoding='utf-8') == 'arquivo de terceiro'
    assert win_secure._journal_path(alvo).exists()


def test_edicao_acima_do_limite_preserva_original(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setattr(win_secure, 'LIMITE_EDICAO', 3)
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('antes', encoding='utf-8')
    with pytest.raises(ValueError, match='limite'):
        win_secure.substituir(alvo, 'antes', 'x')
    assert alvo.read_text(encoding='utf-8') == 'antes'


def test_limpeza_journal_recusa_hardlink(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    journal, identidade = win_secure._guardar_journal(tmp_path / 'alvo.txt', 'segredo', (1, 2, 3))
    alias = journal.with_name('alias')
    os.link(journal, alias)
    with pytest.raises(ValueError, match='links'):
        win_secure._apagar_journal(journal, identidade)
    assert journal.exists() and alias.exists()


def test_falha_fsync_remove_journal_incompleto(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('original', encoding='utf-8')
    def falhar(_fd):
        raise OSError('fsync simulado')
    monkeypatch.setattr(win_secure.os, 'fsync', falhar)
    with pytest.raises(OSError, match='fsync simulado'):
        win_secure._guardar_journal(alvo, 'original', (1, 2, 3))
    assert not win_secure._journal_path(alvo).exists()
    assert alvo.read_text(encoding='utf-8') == 'original'
