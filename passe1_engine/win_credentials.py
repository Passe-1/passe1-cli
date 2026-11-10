"""Leitura e gravação de credenciais com DACL protegida no Windows."""
from __future__ import annotations

import base64
import ctypes
import json
import os
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

from passe1_engine.win_move import _criar_pais_fixo
from passe1_engine.win_private import (
    _AclSize,
    _Attributes,
    _Info,
    _Io,
    _TokenUser,
    _Unicode,
)
from passe1_engine.win_secure import (
    _abrir_journal,
    _ancorar_journal,
    _dpapi,
    _guardar_journal_ancorado,
    _marcar_exclusao_journal,
    snapshot_pais,
)


class RecoveryPendente(ValueError):
    """Credencial não deve ser lida nem alterada até a recuperação local."""


@contextmanager
def _abrir(caminho, *, gravar, ignorar_journal=False, permitir_criar=True):
    if os.name != 'nt':
        raise RuntimeError('Proteção de credenciais Windows indisponível.')
    alvo = Path(os.path.abspath(caminho))
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    a = ctypes.WinDLL('advapi32', use_last_error=True)
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.LocalFree.argtypes = [ctypes.c_void_p]
    k.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Info)]
    k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD]
    k.SetEndOfFile.argtypes = [wintypes.HANDLE]
    k.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    k.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    a.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    a.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    a.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    a.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    a.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int]
    a.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    a.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    n = ctypes.WinDLL('ntdll')
    n.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(_Attributes), ctypes.POINTER(_Io), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    n.NtCreateFile.restype = ctypes.c_long
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
        if not a.ConvertStringSecurityDescriptorToSecurityDescriptorW(f'O:{sid_text.value}D:P(A;;FA;;;{sid_text.value})', 1, ctypes.byref(descricao), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if ':' in alvo.name or alvo.name.endswith((' ', '.')):
            raise ValueError('Nome de credencial inválido.')
        pais = snapshot_pais(alvo)['pais']
        if not gravar and len(pais) != len(alvo.parts) - 1:
            raise FileNotFoundError('Diretório de credenciais inexistente.')
        with _criar_pais_fixo(alvo, pais) as (pai, _):
            buffer = ctypes.create_unicode_buffer(alvo.name)
            tamanho_nome = len(alvo.name.encode('utf-16-le'))
            palavra = _Unicode(tamanho_nome, tamanho_nome + 2, ctypes.cast(buffer, wintypes.LPWSTR))
            acesso = 0xC0130080 if gravar else 0x80120080
            if not ignorar_journal:
                nome_journal = alvo.name + '.passe1-cred-journal'
                buffer_journal = ctypes.create_unicode_buffer(nome_journal)
                bytes_nome = len(nome_journal.encode('utf-16-le'))
                palavra_journal = _Unicode(bytes_nome, bytes_nome + 2, ctypes.cast(buffer_journal, wintypes.LPWSTR))
                attrs_journal = _Attributes(ctypes.sizeof(_Attributes), pai, ctypes.pointer(palavra_journal), 0x40 | 0x1000, None, None)
                handle_journal, ios_journal = wintypes.HANDLE(), _Io()
                estado_journal = n.NtCreateFile(ctypes.byref(handle_journal), 0x100080, ctypes.byref(attrs_journal), ctypes.byref(ios_journal), None, 0, 7, 1, 0x00200060, None, 0)
                if estado_journal >= 0:
                    k.CloseHandle(handle_journal)
                    raise RecoveryPendente('Credencial com recuperação pendente; recupere-a antes de continuar.')
                if estado_journal & 0xffffffff != 0xc0000034:
                    raise RecoveryPendente('Não foi possível verificar a recuperação pendente da credencial.')
            def abrir_relativo(criar):
                attrs = _Attributes(ctypes.sizeof(_Attributes), pai, ctypes.pointer(palavra), 0x40 | 0x1000, descricao if criar else None, None)
                resultado, ios = wintypes.HANDLE(), _Io()
                status = n.NtCreateFile(ctypes.byref(resultado), acesso, ctypes.byref(attrs), ctypes.byref(ios), None, 0, 0, 2 if criar else 1, 0x00200060, None, 0)
                return status, resultado.value
            criado = False
            status, handle = abrir_relativo(False)
            if status < 0:
                if status & 0xffffffff != 0xc0000034:
                    raise OSError(f'Não foi possível abrir credencial: 0x{status & 0xffffffff:08x}')
                if not gravar or not permitir_criar:
                    raise FileNotFoundError('Credencial inexistente.')
                status, handle = abrir_relativo(True)
                if status < 0:
                    raise OSError(f'Não foi possível criar credencial: 0x{status & 0xffffffff:08x}')
                criado = True
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
                    if not dono or not a.EqualSid(sid, dono):
                        raise ValueError('Credencial antiga sem proteção: proprietário diferente; remova-a e faça login novamente.')
                    if not a.GetSecurityDescriptorControl(sec, ctypes.byref(controle), ctypes.byref(revisao)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    if not controle.value & 0x1000:
                        raise ValueError('Credencial antiga sem proteção: DACL herdada; remova-a e faça login novamente.')
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
                yield k, handle, criado, [info.volume, info.index_high, info.index_low]
            except BaseException as exc:
                if criado:
                    excluir = wintypes.BOOL(True)
                    if not k.SetFileInformationByHandle(handle, 4, ctypes.byref(excluir), ctypes.sizeof(excluir)):
                        raise OSError('Não foi possível remover credencial incompleta.') from exc
                raise
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
    with _abrir(caminho, gravar=False) as (kernel, handle, _, _):
        return _ler_handle(kernel, handle)


def _ler_handle(kernel, handle):
    if not kernel.SetFilePointerEx(handle, 0, None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
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
    alvo = Path(os.path.abspath(caminho))
    with _abrir(alvo, gravar=True) as (kernel, handle, criado, identidade):
        anterior = None if criado else _ler_handle(kernel, handle)
        conteudo_journal = 'A' if anterior is None else 'B' + base64.b64encode(anterior).decode('ascii')
        journal = alvo.with_name(alvo.name + '.passe1-cred-journal')
        with _ancorar_journal(alvo.parent):
            _, id_journal = _guardar_journal_ancorado(journal, alvo, conteudo_journal, identidade)
            fd, id_atual = _abrir_journal(journal)
            try:
                if id_atual != id_journal:
                    raise ValueError('Journal da credencial mudou; nenhuma alteração foi feita.')
                with os.fdopen(os.dup(fd), 'rb') as arquivo:
                    guardado = json.loads(_dpapi(arquivo.read(), False).decode('utf-8'))
                if guardado != {'caminho': str(alvo), 'conteudo': conteudo_journal, 'identidade': identidade}:
                    raise ValueError('Journal da credencial inválido; nenhuma alteração foi feita.')
                try:
                    _gravar_handle(kernel, handle, dados)
                except BaseException as exc:
                    if anterior is None:
                        raise OSError('Falha ao gravar a credencial; recuperação local necessária.') from exc
                    try:
                        _gravar_handle(kernel, handle, anterior)
                    except BaseException as falha:
                        raise OSError('Falha na gravação e restauração; recovery de credencial necessário.') from falha
                    _marcar_exclusao_journal(fd)
                    raise OSError('Falha na gravação; estado anterior restaurado.') from exc
                _marcar_exclusao_journal(fd)
            finally:
                os.close(fd)


def _gravar_handle(kernel, handle, dados):
    if not kernel.SetFilePointerEx(handle, 0, None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    for inicio in range(0, len(dados), 65536):
        parte = dados[inicio:inicio + 65536]
        buffer, escritos = ctypes.create_string_buffer(parte), wintypes.DWORD()
        if not kernel.WriteFile(handle, buffer, len(parte), ctypes.byref(escritos), None) or escritos.value != len(parte):
            raise OSError('Não foi possível gravar a credencial.')
    if not kernel.SetEndOfFile(handle) or not kernel.FlushFileBuffers(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def recuperar(caminho, *, confirmado=False):
    """Restaura backup criptografado sem exibir a credencial."""
    if confirmado is not True:
        raise ValueError('Recuperação de credencial exige confirmação explícita.')
    alvo = Path(os.path.abspath(caminho))
    journal = alvo.with_name(alvo.name + '.passe1-cred-journal')
    with _ancorar_journal(alvo.parent):
        fd, _identidade_journal = _abrir_journal(journal)
        try:
            with os.fdopen(os.dup(fd), 'rb') as arquivo:
                cifrado = arquivo.read(2 * 1024 * 1024 + 1)
            if len(cifrado) > 2 * 1024 * 1024:
                raise ValueError('Journal de credencial excede o limite.')
            guardado = json.loads(_dpapi(cifrado, False).decode('utf-8'))
            if (not isinstance(guardado, dict) or not isinstance(guardado.get('caminho'), str)
                    or os.path.normcase(guardado['caminho']) != os.path.normcase(str(alvo))):
                raise ValueError('Journal não corresponde à credencial.')
            conteudo = guardado.get('conteudo')
            if not isinstance(conteudo, str) or not conteudo or conteudo[0] not in ('A', 'B') or (conteudo[0] == 'A' and conteudo != 'A'):
                raise ValueError('Journal de credencial inválido.')
            anterior = None if conteudo == 'A' else base64.b64decode(conteudo[1:], validate=True)
            if anterior is not None and len(anterior) > 1024 * 1024:
                raise ValueError('Credencial excede o limite.')
            try:
                with _abrir(alvo, gravar=True, ignorar_journal=True, permitir_criar=False) as (kernel, handle, _, identidade):
                    if identidade != guardado.get('identidade'):
                        raise ValueError('Identidade da credencial mudou; recuperação recusada.')
                    if anterior is None:
                        excluir = wintypes.BOOL(True)
                        if not kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(excluir), ctypes.sizeof(excluir)):
                            raise ctypes.WinError(ctypes.get_last_error())
                    else:
                        _gravar_handle(kernel, handle, anterior)
                    _marcar_exclusao_journal(fd)
            except FileNotFoundError:
                if anterior is not None:
                    raise
                _marcar_exclusao_journal(fd)
        finally:
            os.close(fd)
