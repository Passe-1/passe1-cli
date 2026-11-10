"""Operações Windows por handles; edição/remoção falham fechadas até suporte seguro."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _dpapi(dados: bytes, proteger: bool) -> bytes:
    """Cifra journal para o usuário atual, sem gravar dados sensíveis em claro."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    memoria = ctypes.create_string_buffer(dados)
    origem = Blob(len(dados), ctypes.cast(memoria, ctypes.POINTER(ctypes.c_byte)))
    destino = Blob()
    operacao = crypt.CryptProtectData if proteger else crypt.CryptUnprotectData
    operacao.restype = wintypes.BOOL
    operacao.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    if not operacao(ctypes.byref(origem), None, None, None, None, 1, ctypes.byref(destino)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(destino.pbData, destino.cbData)
    finally:
        kernel.LocalFree(destino.pbData)


def _journal_path(caminho):
    base = Path(os.environ.get("PASSE1_CONFIG_DIR") or Path.home() / ".config" / "passe1")
    diretorio = base / "recovery"
    diretorio.mkdir(parents=True, exist_ok=True)
    chave = hashlib.sha256(os.path.normcase(os.path.abspath(caminho)).encode("utf-8")).hexdigest()
    return diretorio / (chave + ".dpapi")


def _guardar_journal(caminho, conteudo, identidade):
    arquivo = _journal_path(caminho)
    dados = json.dumps({"caminho": os.path.abspath(caminho), "conteudo": conteudo, "identidade": identidade}, ensure_ascii=False).encode("utf-8")
    cifrado = _dpapi(dados, True)
    fd = os.open(arquivo, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(cifrado)
            f.flush()
            os.fsync(f.fileno())
    except BaseException as exc:
        raise OSError(f"Não foi possível gravar journal; arquivo original não foi alterado. Verifique {arquivo}.") from exc
    return arquivo


def _ler_journal(arquivo):
    return json.loads(_dpapi(Path(arquivo).read_bytes(), False).decode("utf-8"))


def _gravar_handle(kernel, arquivo, texto):
    import ctypes
    from ctypes import wintypes

    dados = texto.encode("utf-8")
    if not kernel.SetFilePointerEx(arquivo, 0, None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(dados)
    escrito = wintypes.DWORD()
    if not kernel.WriteFile(arquivo, buffer, len(dados), ctypes.byref(escrito), None) or escrito.value != len(dados):
        raise OSError("Não foi possível gravar o arquivo completo.")
    if not kernel.SetEndOfFile(arquivo) or not kernel.FlushFileBuffers(arquivo):
        raise ctypes.WinError(ctypes.get_last_error())


def substituir(caminho, anterior, novo):
    if os.name != "nt":
        raise RuntimeError("Operação Windows indisponível nesta plataforma.")
    if anterior is not None:
        _operar(caminho, anterior, novo)
        return
    _criar(caminho, novo)


def remover(caminho, anterior):
    if os.name != "nt":
        raise RuntimeError("Operação Windows indisponível nesta plataforma.")
    _operar(caminho, anterior, None)


def _criar(caminho, conteudo):
    _operar(caminho, None, conteudo)


def recuperar(caminho):
    """Restaura explicitamente backup de edição interrompida do mesmo arquivo."""
    arquivo = _journal_path(caminho)
    dados = _ler_journal(arquivo)
    if os.path.normcase(os.path.abspath(caminho)) != os.path.normcase(dados.get("caminho", "")):
        raise ValueError("Journal pertence a outro caminho.")
    _operar(caminho, "", dados["conteudo"], recuperacao=dados)


def _operar(caminho, anterior, conteudo, recuperacao=None):
    import ctypes
    from ctypes import wintypes

    alvo = Path(os.path.abspath(caminho))
    partes = alvo.parts
    if not alvo.is_absolute() or len(partes) < 2 or any(p in (".", "..") or ":" in p or p.endswith((" ", ".")) for p in partes[1:]):
        raise ValueError("Caminho inválido.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR)]
    class OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE), ("ObjectName", ctypes.POINTER(UNICODE_STRING)), ("Attributes", wintypes.ULONG), ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]
    class IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]
    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", wintypes.FILETIME), ("ftLastAccessTime", wintypes.FILETIME), ("ftLastWriteTime", wintypes.FILETIME), ("dwVolumeSerialNumber", wintypes.DWORD), ("nFileSizeHigh", wintypes.DWORD), ("nFileSizeLow", wintypes.DWORD), ("nNumberOfLinks", wintypes.DWORD), ("nFileIndexHigh", wintypes.DWORD), ("nFileIndexLow", wintypes.DWORD)]
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
    kernel.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetEndOfFile.argtypes = [wintypes.HANDLE]
    kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    ntdll.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(OBJECT_ATTRIBUTES), ctypes.POINTER(IO_STATUS_BLOCK), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    ntdll.NtCreateFile.restype = ctypes.c_long
    root = kernel.CreateFileW(partes[0], 0x100081, 1 | 2, None, 3, 0x02200000, None)
    if root == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    handles = [root]
    def verificar(handle, diretorio):
        info = BY_HANDLE_FILE_INFORMATION()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.dwFileAttributes & 0x400 or bool(info.dwFileAttributes & 0x10) != diretorio:
            raise ValueError("Link ou tipo de arquivo não permitido.")
        if not diretorio and info.nNumberOfLinks != 1:
            raise ValueError("Arquivo com múltiplos links não permitido.")
    def relativo(pai, nome, diretorio, remover_alvo=False):
        buffer = ctypes.create_unicode_buffer(nome)
        tamanho = len(nome.encode("utf-16-le"))
        palavra = UNICODE_STRING(tamanho, tamanho + 2, ctypes.cast(buffer, wintypes.LPWSTR))
        atributos = OBJECT_ATTRIBUTES(ctypes.sizeof(OBJECT_ATTRIBUTES), pai, ctypes.pointer(palavra), 0x40 | 0x1000, None, None)
        resultado = wintypes.HANDLE()
        ios = IO_STATUS_BLOCK()
        # Não compartilhar DELETE impede que ancestrais sejam renomeados durante a operação.
        opcoes = 0x00200000 | 0x20 | (1 if diretorio else 0x40)
        acesso = 0x100081 if diretorio else ((0xC0110180 if conteudo is not None else 0x80110180) if remover_alvo else 0x40110180)
        status = ntdll.NtCreateFile(ctypes.byref(resultado), acesso,
                                   ctypes.byref(atributos), ctypes.byref(ios), None, 0,
                                   1 | 2 if diretorio else 0,
                                   1 if diretorio or remover_alvo else 2, opcoes, None, 0)
        if status < 0:
            raise OSError(f"NtCreateFile falhou: 0x{status & 0xffffffff:08x}")
        handles.append(resultado.value)
        verificar(resultado.value, diretorio)
        return resultado.value
    try:
        verificar(root, True)
        pai = root
        for parte in partes[1:-1]:
            pai = relativo(pai, parte, True)
        arquivo = relativo(pai, partes[-1], False, remover_alvo=anterior is not None)
        if anterior is not None:
            partes_lidas = []
            while True:
                buffer = ctypes.create_string_buffer(65536)
                lidos = wintypes.DWORD()
                if not kernel.ReadFile(arquivo, buffer, len(buffer), ctypes.byref(lidos), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not lidos.value:
                    break
                partes_lidas.append(buffer.raw[:lidos.value])
            if recuperacao is None and b''.join(partes_lidas).decode('utf-8') != anterior:
                raise ValueError("O arquivo mudou após a aprovação.")
            if conteudo is None:
                excluir = wintypes.BOOL(True)
                if not kernel.SetFileInformationByHandle(arquivo, 4, ctypes.byref(excluir), ctypes.sizeof(excluir)):
                    raise ctypes.WinError(ctypes.get_last_error())
                return
            info = BY_HANDLE_FILE_INFORMATION()
            if not kernel.GetFileInformationByHandle(arquivo, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            identidade = (info.dwVolumeSerialNumber, info.nFileIndexHigh, info.nFileIndexLow)
            if recuperacao is not None:
                if list(identidade) != recuperacao.get("identidade"):
                    raise ValueError("Arquivo atual não corresponde ao journal.")
                journal = _journal_path(alvo)
            else:
                journal = _guardar_journal(alvo, anterior, identidade)
            try:
                _gravar_handle(kernel, arquivo, conteudo)
            except BaseException as exc:
                if recuperacao is not None:
                    raise OSError(f"Falha na recuperação; backup criptografado preservado em {journal}.") from exc
                try:
                    _gravar_handle(kernel, arquivo, anterior)
                except BaseException as erro_restauracao:
                    raise OSError(f"Falha na edição e restauração; backup criptografado em {journal}.") from erro_restauracao
                raise OSError(f"Falha na edição; conteúdo anterior restaurado. Backup em {journal}.") from exc
            journal.unlink()
            return
        try:
            dados = conteudo.encode('utf-8')
            buffer = ctypes.create_string_buffer(dados)
            escrito = wintypes.DWORD()
            if not kernel.WriteFile(arquivo, buffer, len(dados), ctypes.byref(escrito), None) or escrito.value != len(dados):
                raise OSError("Não foi possível gravar o arquivo completo.")
            if not kernel.FlushFileBuffers(arquivo):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            excluir = wintypes.BOOL(True)
            if not kernel.SetFileInformationByHandle(arquivo, 4, ctypes.byref(excluir), ctypes.sizeof(excluir)):
                raise OSError("Falha ao remover arquivo incompleto; verifique o destino.") from None
            raise
    finally:
        for handle in reversed(handles):
            kernel.CloseHandle(handle)
