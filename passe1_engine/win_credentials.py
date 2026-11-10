"""Leitura e gravação de credenciais com DACL protegida no Windows."""
from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

from passe1_engine.win_private import _AclSize, _Info, _TokenUser
from passe1_engine.win_secure import _ancorar_journal


class _Seguranca(ctypes.Structure):
    _fields_ = [('tamanho', wintypes.DWORD), ('descritor', ctypes.c_void_p), ('herdar', wintypes.BOOL)]


@contextmanager
def _abrir(caminho, *, gravar):
    if os.name != 'nt':
        raise RuntimeError('Proteção de credenciais Windows indisponível.')
    alvo = Path(os.path.abspath(caminho))
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    a = ctypes.WinDLL('advapi32', use_last_error=True)
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Seguranca), wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k.CreateFileW.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.LocalFree.argtypes = [ctypes.c_void_p]
    k.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Info)]
    k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD]
    k.SetEndOfFile.argtypes = [wintypes.HANDLE]
    k.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    a.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    a.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    a.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    a.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    a.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int]
    a.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    a.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    token, sid_text, descricao = wintypes.HANDLE(), wintypes.LPWSTR(), ctypes.c_void_p()
    if not a.OpenProcessToken(k.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        tamanho = wintypes.DWORD()
        a.GetTokenInformation(token, 1, None, 0, ctypes.byref(tamanho))
        if not tamanho.value:
            raise ctypes.WinError(ctypes.get_last_error())
        usuario = ctypes.create_string_buffer(tamanho.value)
        if not a.GetTokenInformation(token, 1, usuario, len(usuario), ctypes.byref(tamanho)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(usuario, ctypes.POINTER(_TokenUser)).contents.User.Sid
        if not a.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not a.ConvertStringSecurityDescriptorToSecurityDescriptorW(f'D:P(A;;FA;;;{sid_text.value})', 1, ctypes.byref(descricao), None):
            raise ctypes.WinError(ctypes.get_last_error())
        atributos = _Seguranca(ctypes.sizeof(_Seguranca), descricao, False)
        with _ancorar_journal(alvo.parent, criar=gravar):
            acesso = 0xC0020080 if gravar else 0x80020080
            handle = k.CreateFileW(str(alvo), acesso, 0, None, 3, 0x00200000, None)
            if handle == wintypes.HANDLE(-1).value:
                erro = ctypes.get_last_error()
                if not gravar or erro != 2:
                    raise ctypes.WinError(erro)
                handle = k.CreateFileW(str(alvo), acesso, 0, ctypes.byref(atributos), 1, 0x00200000, None)
                if handle == wintypes.HANDLE(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
            try:
                info = _Info()
                if not k.GetFileInformationByHandle(handle, ctypes.byref(info)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if info.attributes & (0x400 | 0x10) or info.links != 1:
                    raise ValueError('Arquivo de credencial inválido ou com links.')
                dono, acl, sec = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
                erro = a.GetSecurityInfo(handle, 1, 5, ctypes.byref(dono), None, ctypes.byref(acl), None, ctypes.byref(sec))
                if erro:
                    raise OSError(erro, 'Não foi possível verificar permissões da credencial.')
                try:
                    controle, revisao, tamanho_acl = wintypes.WORD(), wintypes.DWORD(), _AclSize()
                    if not dono or not a.EqualSid(sid, dono) or not a.GetSecurityDescriptorControl(sec, ctypes.byref(controle), ctypes.byref(revisao)) or not controle.value & 0x1000:
                        raise ValueError('Credencial antiga sem proteção; remova-a e faça login novamente.')
                    if not acl or not a.GetAclInformation(acl, ctypes.byref(tamanho_acl), ctypes.sizeof(tamanho_acl), 2) or tamanho_acl.AceCount != 1:
                        raise ValueError('Credencial antiga sem proteção; remova-a e faça login novamente.')
                    ace = ctypes.c_void_p()
                    if not a.GetAce(acl, 0, ctypes.byref(ace)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    cabecalho = ctypes.string_at(ace, 4)
                    if cabecalho[0] != 0 or cabecalho[1] & 0x10 or not a.EqualSid(sid, ace.value + 8):
                        raise ValueError('Credencial antiga sem proteção; remova-a e faça login novamente.')
                finally:
                    k.LocalFree(sec)
                yield k, handle
            finally:
                k.CloseHandle(handle)
    finally:
        if descricao:
            k.LocalFree(descricao)
        if sid_text:
            k.LocalFree(sid_text)
        k.CloseHandle(token)


def ler(caminho):
    """Lê somente arquivo cuja DACL existente é privada e protegida."""
    with _abrir(caminho, gravar=False) as (kernel, handle):
        partes, total = [], 0
        while True:
            buffer, lidos = ctypes.create_string_buffer(65536), wintypes.DWORD()
            if not kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(lidos), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not lidos.value:
                return b''.join(partes)
            total += lidos.value
            if total > 1024 * 1024:
                raise ValueError('Arquivo de credencial excede 1 MiB.')
            partes.append(buffer.raw[:lidos.value])


def gravar(caminho, dados):
    """Grava bytes somente após validar a DACL no handle exclusivo."""
    if not isinstance(dados, bytes):
        raise TypeError('Credencial deve ser fornecida em bytes.')
    if len(dados) > 1024 * 1024:
        raise ValueError('Arquivo de credencial excede 1 MiB.')
    with _abrir(caminho, gravar=True) as (kernel, handle):
        if not kernel.SetFilePointerEx(handle, 0, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        for inicio in range(0, len(dados), 65536):
            parte = dados[inicio:inicio + 65536]
            buffer, escritos = ctypes.create_string_buffer(parte), wintypes.DWORD()
            if not kernel.WriteFile(handle, buffer, len(parte), ctypes.byref(escritos), None) or escritos.value != len(parte):
                raise OSError('Não foi possível gravar a credencial.')
        if not kernel.SetEndOfFile(handle) or not kernel.FlushFileBuffers(handle):
            raise ctypes.WinError(ctypes.get_last_error())
