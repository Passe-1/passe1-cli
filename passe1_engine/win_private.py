"""Staging Windows privado, com DACL protegida e limpeza por handles."""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
import re
import secrets
import tempfile
from ctypes import wintypes
from pathlib import Path


class _Unicode(ctypes.Structure):
    _fields_ = [('Length', wintypes.USHORT), ('MaximumLength', wintypes.USHORT), ('Buffer', wintypes.LPWSTR)]


class _Attributes(ctypes.Structure):
    _fields_ = [('Length', wintypes.ULONG), ('RootDirectory', wintypes.HANDLE), ('ObjectName', ctypes.POINTER(_Unicode)), ('Attributes', wintypes.ULONG), ('SecurityDescriptor', ctypes.c_void_p), ('SecurityQualityOfService', ctypes.c_void_p)]


class _Io(ctypes.Structure):
    _fields_ = [('Status', ctypes.c_void_p), ('Information', ctypes.c_size_t)]


class _Info(ctypes.Structure):
    _fields_ = [('attributes', wintypes.DWORD), ('created', wintypes.FILETIME), ('accessed', wintypes.FILETIME), ('written', wintypes.FILETIME), ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD), ('size_low', wintypes.DWORD), ('links', wintypes.DWORD), ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]


class _SidAttributes(ctypes.Structure):
    _fields_ = [('Sid', ctypes.c_void_p), ('Attributes', wintypes.DWORD)]


class _TokenUser(ctypes.Structure):
    _fields_ = [('User', _SidAttributes)]


class _AclSize(ctypes.Structure):
    _fields_ = [('AceCount', wintypes.DWORD), ('AclBytesInUse', wintypes.DWORD), ('AclBytesFree', wintypes.DWORD)]


@contextlib.contextmanager
def staging(imagens, parent=None):
    """Cria imagens protegidas e as remove por handle após o uso."""
    if os.name != 'nt':
        raise RuntimeError('Staging Windows indisponível nesta plataforma.')
    if not isinstance(imagens, (list, tuple)) or not imagens:
        raise ValueError('Informe ao menos uma imagem.')
    for extensao, dados in imagens:
        if not isinstance(extensao, str) or not re.fullmatch(r'\.(?:png|jpe?g|gif|webp)', extensao.lower()):
            raise ValueError('Extensão de imagem inválida.')
        if not isinstance(dados, bytes) or not dados:
            raise ValueError('Conteúdo de imagem inválido.')
    if sum(len(dados) for _, dados in imagens) > 4 * 1024 * 1024:
        raise ValueError('Imagens excedem o limite de 4 MiB.')

    # Importações Windows permanecem dentro do fluxo específico da plataforma.
    from passe1_engine.win_secure import _ancorar_journal

    k, a, n = ctypes.WinDLL('kernel32', use_last_error=True), ctypes.WinDLL('advapi32', use_last_error=True), ctypes.WinDLL('ntdll')
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k.CreateFileW.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.LocalFree.argtypes = [ctypes.c_void_p]
    k.LocalFree.restype = ctypes.c_void_p
    k.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Info)]
    k.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    k.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    k.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    a.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    a.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    a.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    a.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int]
    a.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    a.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    n.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(_Attributes), ctypes.POINTER(_Io), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    n.NtCreateFile.restype = ctypes.c_long

    token, sid_text, descricao = wintypes.HANDLE(), wintypes.LPWSTR(), ctypes.c_void_p()
    if not a.OpenProcessToken(k.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        necessario = wintypes.DWORD()
        a.GetTokenInformation(token, 1, None, 0, ctypes.byref(necessario))
        if not necessario.value:
            raise ctypes.WinError(ctypes.get_last_error())
        usuario = ctypes.create_string_buffer(necessario.value)
        if not a.GetTokenInformation(token, 1, usuario, len(usuario), ctypes.byref(necessario)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(usuario, ctypes.POINTER(_TokenUser)).contents.User.Sid
        if not a.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise ctypes.WinError(ctypes.get_last_error())
        sddl = f'D:P(A;OICI;FA;;;{sid_text.value})'
        if not a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descricao), None):
            raise ctypes.WinError(ctypes.get_last_error())

        def dacl_privada(handle):
            acl, sec = ctypes.c_void_p(), ctypes.c_void_p()
            erro = a.GetSecurityInfo(handle, 1, 4, None, None, ctypes.byref(acl), None, ctypes.byref(sec))
            if erro:
                raise OSError(erro, 'Não foi possível verificar a DACL.')
            try:
                tamanho = _AclSize()
                if not acl or not a.GetAclInformation(acl, ctypes.byref(tamanho), ctypes.sizeof(tamanho), 2) or tamanho.AceCount != 1:
                    raise ValueError('DACL do staging não é privada.')
                ace = ctypes.c_void_p()
                if not a.GetAce(acl, 0, ctypes.byref(ace)):
                    raise ctypes.WinError(ctypes.get_last_error())
                cabecalho = ctypes.string_at(ace, 4)
                if cabecalho[0] != 0 or cabecalho[1] & 0x10 or not a.EqualSid(sid, ace.value + 8):
                    raise ValueError('DACL do staging concede acesso inesperado.')
            finally:
                k.LocalFree(sec)

        def abrir_relativo(pai, nome, acesso, compartilhar, criar, diretorio):
            buffer = ctypes.create_unicode_buffer(nome)
            tamanho = len(nome.encode('utf-16-le'))
            palavra = _Unicode(tamanho, tamanho + 2, ctypes.cast(buffer, wintypes.LPWSTR))
            attrs = _Attributes(ctypes.sizeof(_Attributes), pai, ctypes.pointer(palavra), 0x40 | 0x1000, descricao if criar else None, None)
            resultado, ios = wintypes.HANDLE(), _Io()
            opcoes = 0x00200020 | (1 if diretorio else 0x40)
            status = n.NtCreateFile(ctypes.byref(resultado), acesso, ctypes.byref(attrs), ctypes.byref(ios), None, 0, compartilhar, 2 if criar else 1, opcoes, None, 0)
            if status < 0:
                raise OSError(f'NtCreateFile falhou: 0x{status & 0xffffffff:08x}')
            return resultado.value

        def info(handle, diretorio):
            valor = _Info()
            if not k.GetFileInformationByHandle(handle, ctypes.byref(valor)):
                raise ctypes.WinError(ctypes.get_last_error())
            if valor.attributes & 0x400 or bool(valor.attributes & 0x10) != diretorio or (not diretorio and valor.links != 1):
                raise ValueError('Entrada de staging inválida ou com links.')
            return valor.volume, valor.index_high, valor.index_low

        def apagar(handle):
            excluir = wintypes.BOOL(True)
            if not k.SetFileInformationByHandle(handle, 4, ctypes.byref(excluir), ctypes.sizeof(excluir)):
                raise ctypes.WinError(ctypes.get_last_error())

        def caminho_final(handle):
            tamanho = k.GetFinalPathNameByHandleW(handle, None, 0, 0)
            if not tamanho:
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_unicode_buffer(tamanho + 1)
            escrito = k.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
            if not escrito or escrito >= len(buffer):
                raise OSError('Não foi possível verificar o diretório de staging.')
            final = buffer.value
            if final.startswith('\\\\?\\UNC\\'):
                final = '\\\\' + final[8:]
            elif final.startswith('\\\\?\\'):
                final = final[4:]
            return os.path.normcase(os.path.normpath(final))

        pai_path = Path(os.path.abspath(parent or tempfile.gettempdir()))
        nome_dir = 'passe1-' + secrets.token_hex(16)
        caminhos, arquivos = [], []
        diretorio = None
        with _ancorar_journal(pai_path):
            pai = k.CreateFileW(str(pai_path), 0x100084, 3, None, 3, 0x02200000, None)
            if pai == wintypes.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                info(pai, True)
                diretorio = abrir_relativo(pai, nome_dir, 0x110087 | 0x20000, 3, True, True)
                info(diretorio, True)
                dacl_privada(diretorio)
                for indice, (extensao, dados) in enumerate(imagens):
                    nome = f'{indice}{extensao.lower()}'
                    escrita = abrir_relativo(diretorio, nome, 0x40110080 | 0x20000, 3, True, False)
                    try:
                        identidade = info(escrita, False)
                        dacl_privada(escrita)
                        buffer, gravado = ctypes.create_string_buffer(dados), wintypes.DWORD()
                        if not k.WriteFile(escrita, buffer, len(dados), ctypes.byref(gravado), None) or gravado.value != len(dados) or not k.FlushFileBuffers(escrita):
                            raise OSError('Falha ao gravar imagem de staging.')
                    except BaseException:
                        apagar(escrita)
                        raise
                    finally:
                        k.CloseHandle(escrita)
                    arquivos.append((nome, identidade, None))
                    leitura = abrir_relativo(diretorio, nome, 0x80100080 | 0x20000, 1, False, False)
                    arquivos[-1] = (nome, identidade, leitura)
                    if info(leitura, False) != identidade:
                        raise ValueError('Imagem mudou durante a preparação.')
                    dacl_privada(leitura)
                    digest = hashlib.sha256()
                    while True:
                        parte, lidos = ctypes.create_string_buffer(65536), wintypes.DWORD()
                        if not k.ReadFile(leitura, parte, len(parte), ctypes.byref(lidos), None):
                            raise ctypes.WinError(ctypes.get_last_error())
                        if not lidos.value:
                            break
                        digest.update(parte.raw[:lidos.value])
                    if digest.digest() != hashlib.sha256(dados).digest():
                        raise ValueError('Conteúdo da imagem mudou durante a preparação.')
                    caminhos.append(pai_path / nome_dir / nome)
                if caminho_final(diretorio) != os.path.normcase(os.path.normpath(os.path.join(caminho_final(pai), nome_dir))):
                    raise ValueError('Diretório de staging mudou durante a preparação.')
                yield caminhos
            finally:
                falha = None
                for nome, identidade, leitura in reversed(arquivos):
                    if leitura is not None:
                        k.CloseHandle(leitura)
                    try:
                        apagar_handle = abrir_relativo(diretorio, nome, 0x100080 | 0x10000, 0, False, False)
                        try:
                            if info(apagar_handle, False) != identidade:
                                raise ValueError('Imagem mudou; não foi removida.')
                            apagar(apagar_handle)
                        finally:
                            k.CloseHandle(apagar_handle)
                    except (OSError, ValueError) as exc:
                        falha = exc
                if diretorio is not None:
                    try:
                        apagar(diretorio)
                    except OSError as exc:
                        falha = exc
                    finally:
                        k.CloseHandle(diretorio)
                k.CloseHandle(pai)
                if falha is not None:
                    raise OSError(f'Não foi possível limpar staging privado: {pai_path / nome_dir}') from falha
    finally:
        if descricao:
            k.LocalFree(descricao)
        if sid_text:
            k.LocalFree(sid_text)
        k.CloseHandle(token)
