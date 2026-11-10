"""Operações Windows por handles; edição/remoção falham fechadas até suporte seguro."""
from __future__ import annotations

import os
from pathlib import Path


def substituir(caminho, anterior, novo):
    if os.name != "nt":
        raise RuntimeError("Operação Windows indisponível nesta plataforma.")
    if anterior is not None:
        raise RuntimeError("Edição segura Windows ainda não está disponível.")
    _criar(caminho, novo)


def remover(caminho, anterior):
    if os.name != "nt":
        raise RuntimeError("Operação Windows indisponível nesta plataforma.")
    _operar(caminho, anterior, None)


def _criar(caminho, conteudo):
    _operar(caminho, None, conteudo)


def _operar(caminho, anterior, conteudo):
    import ctypes
    from ctypes import wintypes

    alvo = Path(os.path.abspath(caminho))
    partes = alvo.parts
    if not alvo.is_absolute() or len(partes) < 2 or any(p in (".", "..") for p in partes[1:]):
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
        acesso = 0x100081 if diretorio else (0x80110100 if remover_alvo else 0x40110100)
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
            if b''.join(partes_lidas).decode('utf-8') != anterior:
                raise ValueError("O arquivo mudou após a aprovação.")
            excluir = wintypes.BOOL(True)
            if not kernel.SetFileInformationByHandle(arquivo, 4, ctypes.byref(excluir), ctypes.sizeof(excluir)):
                raise ctypes.WinError(ctypes.get_last_error())
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
