"""Bases experimentais para movimentação recuperável no Windows.

Ainda não publica operações de movimentação: o protocolo de handles e journal
deve ser implementado e validado nos runners antes da integração.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from contextlib import ExitStack, contextmanager
from pathlib import Path

from passe1_engine.win_secure import (
    LIMITE_EDICAO,
    _ancorar_journal,
    _dpapi,
    _guardar_journal_ancorado,
    _marcar_exclusao_journal,
    snapshot_pais,
)
from passe1_engine.win_secure import (
    _abrir_journal as _abrir_journal_handle,
)

_revisoes = {}
LIMITE_MOVE_REVISAVEL = 1024 * 1024


def _digest(texto):
    if not isinstance(texto, str):
        raise TypeError('Conteúdo inválido para movimentação.')
    dados = texto.encode('utf-8')
    if len(dados) > LIMITE_EDICAO:
        raise ValueError('Conteúdo excede o limite de 64 MiB para movimentação.')
    return hashlib.sha256(dados).hexdigest()


def _verificar_tamanho_revisavel(*textos):
    if any(not isinstance(texto, str) for texto in textos):
        raise TypeError('Conteúdo inválido para movimentação.')
    if sum(len(texto.encode('utf-8')) for texto in textos) > LIMITE_MOVE_REVISAVEL:
        raise ValueError('Move exige recuperação revisável; conteúdo total excede 1 MiB. Nenhum arquivo foi alterado.')


def _estado(origem, destino, plano):
    """Identifica estados conhecidos por identidade física e hash de conteúdo.

    Uma entrada presente é um par ``(identidade, hash)`` obtido do mesmo
    handle. Uma entrada ausente é ``None``. Outros estados exigem revisão.
    """
    esperado_o = (plano['id_origem'], plano['hash_origem'])
    esperado_d = (plano['id_destino'], plano['hash_destino'])
    novo_d = (plano['id_destino'], plano['hash_novo'])

    def igual(atual, esperado):
        return (isinstance(atual, tuple) and len(atual) == 2
                and atual[0] == esperado[0]
                and isinstance(atual[1], str)
                and hmac.compare_digest(atual[1], esperado[1]))

    if igual(origem, esperado_o) and igual(destino, esperado_d):
        return 'original'
    if igual(origem, esperado_o) and igual(destino, novo_d):
        return 'destino_atualizado'
    if origem is None and igual(destino, novo_d):
        return 'aplicado'
    return 'divergente'


def _escrita_interrompida(anterior, novo, atual):
    """Reconhece prefixos possíveis da escrita sequencial antes do truncate.

    Só identifica uma forma compatível; recuperação ainda exige identidade,
    journal íntegro e confirmação do usuário.
    """
    if not all(isinstance(x, bytes) for x in (anterior, novo, atual)):
        raise TypeError('Conteúdo de recuperação deve estar em bytes.')
    if atual == novo:
        return True
    if len(atual) > len(anterior):
        return len(atual) <= len(novo) and atual == novo[:len(atual)]
    if len(atual) != len(anterior):
        return False
    ultimo_diferente_do_anterior = -1
    primeiro_diferente_do_novo = len(atual)
    for indice, byte in enumerate(atual):
        if byte != anterior[indice]:
            ultimo_diferente_do_anterior = indice
        if indice >= len(novo) or byte != novo[indice]:
            primeiro_diferente_do_novo = min(primeiro_diferente_do_novo, indice)
    return ultimo_diferente_do_anterior < primeiro_diferente_do_novo


def _gravar_handle(fd, dados):
    """Escreve e sincroniza no handle fixado; requer journal prévio do chamador."""
    if not isinstance(dados, bytes):
        raise TypeError('Conteúdo deve estar em bytes.')
    if len(dados) > LIMITE_EDICAO:
        raise ValueError('Conteúdo excede o limite de 64 MiB para movimentação.')
    os.lseek(fd, 0, os.SEEK_SET)
    posicao = 0
    while posicao < len(dados):
        gravados = os.write(fd, dados[posicao:])
        if gravados <= 0:
            raise OSError('Escrita incompleta durante movimentação.')
        posicao += gravados
    os.ftruncate(fd, len(dados))
    os.fsync(fd)


def _restaurar_conteudo(fd, anterior, novo, *, confirmado=False):
    """Restaura somente bytes compatíveis, após validação do handle pelo chamador."""
    if confirmado is not True:
        raise ValueError('Recuperação exige confirmação explícita.')
    if not isinstance(anterior, bytes) or not isinstance(novo, bytes):
        raise TypeError('Conteúdo de recuperação deve estar em bytes.')
    if max(len(anterior), len(novo)) > LIMITE_EDICAO:
        raise ValueError('Conteúdo excede o limite de 64 MiB para movimentação.')
    os.lseek(fd, 0, os.SEEK_SET)
    partes, tamanho = [], 0
    while parte := os.read(fd, 65536):
        tamanho += len(parte)
        if tamanho > LIMITE_EDICAO:
            raise ValueError('Conteúdo excede o limite de 64 MiB para movimentação.')
        partes.append(parte)
    atual = b''.join(partes)
    if not (_escrita_interrompida(anterior, novo, atual) or _escrita_interrompida(novo, anterior, atual)):
        raise ValueError('Conteúdo divergiu; recuperação recusada.')
    _gravar_handle(fd, anterior)


def _validar_journal(dados):
    if not isinstance(dados, dict) or dados.get('versao') != 1:
        raise ValueError('Journal de movimentação inválido.')
    ausente = dados.get('destino_ausente', False)
    if not isinstance(ausente, bool):
        raise TypeError('Estado do destino inválido no journal.')
    if any(chave not in dados for chave in ('id_destino', 'conteudo_destino', 'hash_destino')):
        raise ValueError('Journal de movimentação incompleto.')
    for nome in ('origem', 'destino'):
        caminho = dados.get(nome)
        if not isinstance(caminho, str) or not os.path.isabs(caminho):
            raise ValueError('Caminho inválido no journal.')
        identidade = dados.get('id_' + nome)
        if nome == 'destino' and ausente and identidade is not None:
            raise ValueError('Identidade inválida para destino ausente.')
        if not (nome == 'destino' and ausente and identidade is None) and (not isinstance(identidade, list) or len(identidade) != 3 or any(not isinstance(v, str) or not v.isdecimal() for v in identidade)):
            raise ValueError('Identidade inválida no journal.')
        pais = dados.get('pais_' + nome)
        if not isinstance(pais, list) or not pais or any(
            not isinstance(pai, list) or len(pai) != 3
            or any(not isinstance(v, str) or not v.isdecimal() for v in pai)
            for pai in pais
        ):
            raise ValueError('Identidade dos diretórios inválida no journal.')
    if os.path.normcase(dados['origem']) == os.path.normcase(dados['destino']) or dados['id_origem'] == dados['id_destino']:
        raise ValueError('Origem e destino não podem ser o mesmo arquivo.')
    for nome in ('origem', 'destino', 'novo'):
        texto = dados.get('conteudo_' + nome)
        digest = dados.get('hash_' + nome)
        if nome == 'destino' and ausente:
            if texto is not None or digest is not None:
                raise ValueError('Conteúdo inválido para destino ausente.')
            continue
        if not isinstance(digest, str) or not hmac.compare_digest(_digest(texto), digest):
            raise ValueError('Conteúdo do journal não corresponde ao hash.')
    return dados


def _proteger_journal(dados):
    validado = _validar_journal(dados)
    serializado = json.dumps(validado, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return _dpapi(serializado, True)


def _abrir_journal(dados):
    bruto = _dpapi(dados, False)
    try:
        conteudo = json.loads(bruto.decode('utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise ValueError('Journal de movimentação inválido.') from exc
    return _validar_journal(conteudo)


def _serializar_plano(plano):
    """Recusa antes de gravar um journal que a leitura limitada não aceitaria."""
    serializado = json.dumps(plano, ensure_ascii=False, separators=(',', ':'))
    envelope = json.dumps({
        'caminho': os.path.abspath(plano['origem']),
        'conteudo': serializado,
        'identidade': plano['id_origem'],
    }, ensure_ascii=False).encode('utf-8')
    # DPAPI acrescenta cabeçalho e autenticação; reservar espaço evita criar
    # journal maior que o limite aceito na recuperação.
    if len(envelope) > LIMITE_EDICAO * 4 - 4096:
        raise ValueError('Journal excede o limite de recuperação.')
    return serializado


@contextmanager
def _journal_fixo(raiz, plano):
    """Experimento: grava journal cifrado e mantém arquivo e ancestrais abertos.

    Não autoriza alterações em arquivos: durabilidade após queda do sistema e
    recuperação transacional ainda precisam ser comprovadas no Windows.
    """
    plano = _validar_journal(plano)
    raiz = Path(os.path.abspath(raiz))
    nome = 'move-' + secrets.token_hex(16) + '.journal'
    arquivo = raiz / nome
    with _ancorar_journal(raiz, criar=True):
        serializado = _serializar_plano(plano)
        _, identidade = _guardar_journal_ancorado(arquivo, plano['origem'], serializado, plano['id_origem'])
        fd, atual = _abrir_journal_handle(arquivo)
        try:
            if atual != identidade:
                raise ValueError('Journal mudou durante a preparação.')
            if os.fstat(fd).st_size > LIMITE_EDICAO * 4:
                raise ValueError('Journal excede o limite de recuperação; nenhum arquivo foi alterado.')
            partes = []
            while parte := os.read(fd, 65536):
                partes.append(parte)
            documento = json.loads(_dpapi(b''.join(partes), False).decode('utf-8'))
            if documento.get('caminho') != os.path.abspath(plano['origem']) or documento.get('identidade') != plano['id_origem']:
                raise ValueError('Identidade do journal mudou durante a preparação.')
            validado = _validar_journal(json.loads(documento['conteudo']))
            if validado != plano:
                raise ValueError('Conteúdo do journal mudou durante a preparação.')
            yield arquivo, identidade
        finally:
            os.close(fd)


@contextmanager
def _par_fixo(origem, destino, plano, *, em_quarentena=False, permitir_parcial=False):
    """Experimento: mantém ambos arquivos aprovados abertos sem compartilhamento."""
    if os.name != 'nt':
        raise RuntimeError('Movimentação Windows indisponível nesta plataforma.')
    import ctypes
    import msvcrt
    from ctypes import wintypes

    from passe1_engine.win_private import _Attributes, _Info, _Io, _Unicode

    plano = _validar_journal(plano)
    caminhos = (Path(os.path.abspath(origem)), Path(os.path.abspath(destino)))
    if tuple(map(str, caminhos)) != (plano['origem'], plano['destino']):
        raise ValueError('Caminhos não correspondem ao journal aprovado.')
    if em_quarentena:
        quarentena = plano.get('quarentena')
        if not isinstance(quarentena, str) or not re.fullmatch(r'passe1-move-[0-9a-f]{32}', quarentena):
            raise ValueError('Nome de quarentena inválido no journal.')
        caminhos = (caminhos[0].with_name(quarentena), caminhos[1])
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Info)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll = ctypes.WinDLL('ntdll')
    ntdll.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(_Attributes), ctypes.POINTER(_Io), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    ntdll.NtCreateFile.restype = ctypes.c_long
    descritores = {}
    diretorios = {}
    with ExitStack() as pilha:
        for pai in sorted({c.parent for c in caminhos}, key=lambda p: os.path.normcase(str(p))):
            pilha.enter_context(_ancorar_journal(pai))
        for nome, caminho in zip(('origem', 'destino'), caminhos, strict=True):
            if snapshot_pais(caminho)['pais'] != plano['pais_' + nome]:
                raise ValueError('Diretório mudou após a aprovação.')
            pai = kernel.CreateFileW(str(caminho.parent), 0x100084, 3, None, 3, 0x02200000, None)
            if pai == wintypes.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            pilha.callback(kernel.CloseHandle, pai)
            informacao_pai = _Info()
            if not kernel.GetFileInformationByHandle(pai, ctypes.byref(informacao_pai)):
                raise ctypes.WinError(ctypes.get_last_error())
            id_pai = [str(informacao_pai.volume), str(informacao_pai.index_high), str(informacao_pai.index_low)]
            if informacao_pai.attributes & 0x400 or not informacao_pai.attributes & 0x10 or id_pai != plano['pais_' + nome][-1]:
                raise ValueError('Diretório mudou após a aprovação.')
            diretorios[nome] = pai
        for nome, caminho in sorted(zip(('origem', 'destino'), caminhos, strict=True), key=lambda par: os.path.normcase(str(par[1]))):
            buffer = ctypes.create_unicode_buffer(caminho.name)
            tamanho = len(caminho.name.encode('utf-16-le'))
            palavra = _Unicode(tamanho, tamanho + 2, ctypes.cast(buffer, wintypes.LPWSTR))
            attrs = _Attributes(ctypes.sizeof(_Attributes), diretorios[nome], ctypes.pointer(palavra), 0x40 | 0x1000, None, None)
            resultado, ios = wintypes.HANDLE(), _Io()
            status = ntdll.NtCreateFile(ctypes.byref(resultado), 0xC0110080, ctypes.byref(attrs), ctypes.byref(ios), None, 0, 0, 1, 0x00200060, None, 0)
            if status < 0:
                raise OSError(f'NtCreateFile do arquivo falhou: 0x{status & 0xffffffff:08x}')
            handle = resultado.value
            try:
                informacao = _Info()
                if not kernel.GetFileInformationByHandle(handle, ctypes.byref(informacao)):
                    raise ctypes.WinError(ctypes.get_last_error())
                identidade = [str(informacao.volume), str(informacao.index_high), str(informacao.index_low)]
                if informacao.attributes & (0x400 | 0x10) or informacao.links != 1 or identidade != plano['id_' + nome]:
                    raise ValueError('Arquivo mudou após a aprovação ou possui links.')
                fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
                handle = None
                pilha.callback(os.close, fd)
                partes, tamanho = [], 0
                while parte := os.read(fd, 65536):
                    tamanho += len(parte)
                    if tamanho > LIMITE_EDICAO:
                        raise ValueError('Conteúdo excede o limite de 64 MiB para movimentação.')
                    partes.append(parte)
                atual = b''.join(partes)
                igual = hmac.compare_digest(hashlib.sha256(atual).hexdigest(), plano['hash_' + nome])
                if not igual and nome == 'destino' and permitir_parcial:
                    antigo = plano['conteudo_destino'].encode('utf-8')
                    novo = plano['conteudo_novo'].encode('utf-8')
                    igual = _escrita_interrompida(antigo, novo, atual) or _escrita_interrompida(novo, antigo, atual)
                if not igual:
                    raise ValueError('Conteúdo mudou após a aprovação.')
                os.lseek(fd, 0, os.SEEK_SET)
                descritores[nome] = fd
            finally:
                if handle is not None:
                    kernel.CloseHandle(handle)
        for nome, caminho in zip(('origem', 'destino'), caminhos, strict=True):
            if snapshot_pais(caminho)['pais'] != plano['pais_' + nome]:
                raise ValueError('Diretório mudou durante a preparação.')
        yield descritores, diretorios


def _renomear_handle(fd, diretorio, nome):
    """Experimento: renomeia por handle, sem substituir entrada existente."""
    if os.name != 'nt':
        raise RuntimeError('Movimentação Windows indisponível nesta plataforma.')
    if not isinstance(nome, str) or nome in ('', '.', '..') or any(c in nome for c in '\\/:<>|?*') or nome.endswith((' ', '.')):
        raise ValueError('Nome de quarentena inválido.')
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class Io(ctypes.Structure):
        _fields_ = [('Status', ctypes.c_void_p), ('Information', ctypes.c_size_t)]

    class Rename(ctypes.Structure):
        _fields_ = [('ReplaceIfExists', wintypes.BOOL), ('RootDirectory', wintypes.HANDLE),
                    ('FileNameLength', wintypes.DWORD), ('FileName', wintypes.WCHAR * (len(nome.encode('utf-16-le')) // 2 + 1))]

    dados = Rename(False, diretorio, len(nome.encode('utf-16-le')), nome)
    io = Io()
    ntdll = ctypes.WinDLL('ntdll')
    ntdll.NtSetInformationFile.argtypes = [wintypes.HANDLE, ctypes.POINTER(Io), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG]
    ntdll.NtSetInformationFile.restype = ctypes.c_long
    status = ntdll.NtSetInformationFile(msvcrt.get_osfhandle(fd), ctypes.byref(io), ctypes.byref(dados), ctypes.sizeof(dados), 10)
    if status < 0:
        raise OSError(f'NtSetInformationFile falhou: 0x{status & 0xffffffff:08x}')


@contextmanager
def _preparar_arquivo_temporario(destino, pais_aprovados, *, _nome=None, _descartar_ao_fechar=True):
    """Experimento para destino ausente: temporário some ao fechar seu handle.

    Não publica o arquivo nem cria diretórios; não integra o fluxo de move.
    """
    if os.name != 'nt':
        raise RuntimeError('Movimentação Windows indisponível nesta plataforma.')
    import ctypes
    import msvcrt
    from ctypes import wintypes

    from passe1_engine.win_private import _Attributes, _Info, _Io, _Unicode

    caminho = Path(os.path.abspath(destino))
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Info)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll = ctypes.WinDLL('ntdll')
    ntdll.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(_Attributes), ctypes.POINTER(_Io), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    ntdll.NtCreateFile.restype = ctypes.c_long
    nome = _nome or 'passe1-stage-' + secrets.token_hex(16)
    if not re.fullmatch(r'passe1-stage-[0-9a-f]{32}', nome):
        raise ValueError('Nome de temporário inválido.')
    with _ancorar_journal(caminho.parent):
        if snapshot_pais(caminho)['pais'] != pais_aprovados:
            raise ValueError('Diretório mudou após a aprovação.')
        pai = kernel.CreateFileW(str(caminho.parent), 0x100084, 3, None, 3, 0x02200000, None)
        if pai == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info_pai = _Info()
            if not kernel.GetFileInformationByHandle(pai, ctypes.byref(info_pai)):
                raise ctypes.WinError(ctypes.get_last_error())
            id_pai = [str(info_pai.volume), str(info_pai.index_high), str(info_pai.index_low)]
            if info_pai.attributes & 0x400 or not info_pai.attributes & 0x10 or not pais_aprovados or id_pai != pais_aprovados[-1]:
                raise ValueError('Diretório mudou após a aprovação.')
            buffer = ctypes.create_unicode_buffer(nome)
            tamanho = len(nome.encode('utf-16-le'))
            palavra = _Unicode(tamanho, tamanho + 2, ctypes.cast(buffer, wintypes.LPWSTR))
            atributos = _Attributes(ctypes.sizeof(_Attributes), pai, ctypes.pointer(palavra), 0x40 | 0x1000, None, None)
            handle, ios = wintypes.HANDLE(), _Io()
            opcoes = 0x00200060 | (0x1000 if _descartar_ao_fechar else 0)
            status = ntdll.NtCreateFile(ctypes.byref(handle), 0xC0110180, ctypes.byref(atributos), ctypes.byref(ios), None, 0, 0, 2, opcoes, None, 0)
            if status < 0:
                raise OSError(f'NtCreateFile do temporário falhou: 0x{status & 0xffffffff:08x}')
            try:
                info = _Info()
                if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if info.attributes & (0x400 | 0x10) or info.links != 1:
                    raise ValueError('Temporário inválido ou com links.')
                fd = msvcrt.open_osfhandle(handle.value, os.O_RDWR | os.O_BINARY)
                handle = None
                try:
                    yield fd, pai, nome, [str(info.volume), str(info.index_high), str(info.index_low)]
                finally:
                    os.close(fd)
            finally:
                if handle is not None:
                    kernel.CloseHandle(handle)
        finally:
            kernel.CloseHandle(pai)


@contextmanager
def _criar_pais_fixo(destino, pais_aprovados):
    """Experimento: cria pais faltantes por handles após a aprovação.

    Diretórios criados não são apagados automaticamente após uma queda; a
    identidade completa deve ser registrada antes de publicar arquivos.
    """
    if os.name != 'nt':
        raise RuntimeError('Movimentação Windows indisponível nesta plataforma.')
    import ctypes
    from ctypes import wintypes

    from passe1_engine.win_private import _Attributes, _Info, _Io, _Unicode

    caminho = Path(os.path.abspath(destino))
    partes = caminho.parts
    if not isinstance(pais_aprovados, list) or not pais_aprovados or len(pais_aprovados) > len(partes) - 1:
        raise ValueError('Identidades de diretórios inválidas.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Info)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll = ctypes.WinDLL('ntdll')
    ntdll.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(_Attributes), ctypes.POINTER(_Io), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    ntdll.NtCreateFile.restype = ctypes.c_long

    def identidade(handle):
        info = _Info()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400 or not info.attributes & 0x10:
            raise ValueError('Diretório inválido ou com reparse.')
        return [str(info.volume), str(info.index_high), str(info.index_low)]

    def acesso_pai(indice):
        ultimo = len(partes) - 2
        acesso = 0x100081
        if indice == ultimo:
            acesso |= 2  # FILE_ADD_FILE
        elif indice >= len(pais_aprovados) - 1:
            acesso |= 4  # FILE_ADD_SUBDIRECTORY
        if indice >= len(pais_aprovados):
            acesso |= 0x10000  # DELETE para identificação e recuperação futuras
        return acesso

    raiz = kernel.CreateFileW(partes[0], acesso_pai(0), 3, None, 3, 0x02200000, None)
    if raiz == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    handles = [raiz]
    try:
        ids = [identidade(raiz)]
        if ids[0] != pais_aprovados[0]:
            raise ValueError('Raiz mudou após aprovação.')
        pai = raiz
        for indice, componente in enumerate(partes[1:-1], 1):
            if componente in ('.', '..') or ':' in componente or componente.endswith((' ', '.')):
                raise ValueError('Componente inválido.')
            criando = indice >= len(pais_aprovados)
            buffer = ctypes.create_unicode_buffer(componente)
            tamanho = len(componente.encode('utf-16-le'))
            palavra = _Unicode(tamanho, tamanho + 2, ctypes.cast(buffer, wintypes.LPWSTR))
            attrs = _Attributes(ctypes.sizeof(_Attributes), pai, ctypes.pointer(palavra), 0x40 | 0x1000, None, None)
            resultado, ios = wintypes.HANDLE(), _Io()
            acesso = acesso_pai(indice)
            status = ntdll.NtCreateFile(ctypes.byref(resultado), acesso, ctypes.byref(attrs), ctypes.byref(ios), None, 0, 3, 2 if criando else 1, 0x00200021, None, 0)
            if status < 0:
                raise OSError(f'NtCreateFile do diretório falhou: 0x{status & 0xffffffff:08x}')
            pai = resultado.value
            handles.append(pai)
            ids.append(identidade(pai))
            if not criando and ids[-1] != pais_aprovados[indice]:
                raise ValueError('Diretório mudou após aprovação.')
        yield pai, ids
    finally:
        for handle in reversed(handles):
            kernel.CloseHandle(handle)


@contextmanager
def _criar_stage_relativo(pai, nome):
    """Cria stage persistente exclusivamente relativo ao diretório fixado."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    from passe1_engine.win_private import _Attributes, _Info, _Io, _Unicode

    if not isinstance(nome, str) or not re.fullmatch(r'passe1-stage-[0-9a-f]{32}', nome):
        raise ValueError('Nome de stage inválido.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Info)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll = ctypes.WinDLL('ntdll')
    ntdll.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(_Attributes), ctypes.POINTER(_Io), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    ntdll.NtCreateFile.restype = ctypes.c_long
    buffer = ctypes.create_unicode_buffer(nome)
    tamanho = len(nome.encode('utf-16-le'))
    palavra = _Unicode(tamanho, tamanho + 2, ctypes.cast(buffer, wintypes.LPWSTR))
    attrs = _Attributes(ctypes.sizeof(_Attributes), pai, ctypes.pointer(palavra), 0x40 | 0x1000, None, None)
    handle, ios = wintypes.HANDLE(), _Io()
    status = ntdll.NtCreateFile(ctypes.byref(handle), 0xC0110180, ctypes.byref(attrs), ctypes.byref(ios), None, 0, 0, 2, 0x00200060, None, 0)
    if status < 0:
        raise OSError(f'NtCreateFile do stage falhou: 0x{status & 0xffffffff:08x}')
    try:
        info = _Info()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (0x400 | 0x10) or info.links != 1:
            raise ValueError('Stage inválido ou com links.')
        fd = msvcrt.open_osfhandle(handle.value, os.O_RDWR | os.O_BINARY)
        handle = None
        try:
            yield fd, [str(info.volume), str(info.index_high), str(info.index_low)]
        finally:
            os.close(fd)
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)


@contextmanager
def _preparar_stage_registrado(journal):
    """Cria etapa persistente somente após fixar journal com nome aprovado.

    No encerramento normal, grava segundo registro cifrado e sincronizado
    com a identidade e hash do stage antes de fechar o handle. Não publica.
    """
    with _abrir_plano_fixo(journal) as (arquivo, id_journal, plano):
        nome = plano.get('stage')
        if plano.get('destino_ausente') is not True or not isinstance(nome, str) or not re.fullmatch(r'passe1-stage-[0-9a-f]{32}', nome):
            raise ValueError('Plano de destino ausente inválido.')
        with _criar_pais_fixo(plano['destino'], plano['pais_destino']) as (pai, pais_atuais), _criar_stage_relativo(pai, nome) as (fd, id_stage):
            yield fd, pai, nome, id_stage
            if not hmac.compare_digest(hashlib.sha256(_conteudo_handle(fd)).hexdigest(), plano['hash_novo']):
                raise ValueError('Conteúdo do stage divergiu do plano aprovado.')
            registro = {
                'versao': 1, 'journal': list(id_journal), 'stage': nome,
                'id_stage': id_stage, 'pais_destino': pais_atuais,
                'hash_novo': plano['hash_novo'],
                'hash_plano': hashlib.sha256(json.dumps(plano, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest(),
            }
            _guardar_journal_ancorado(Path(str(arquivo) + '.stage'), plano['destino'], json.dumps(registro, ensure_ascii=False), id_stage)


@contextmanager
def _abrir_registro_stage(journal):
    """Fixa e cruza os dois registros antes de qualquer publicação futura."""
    with _abrir_plano_fixo(journal) as (arquivo, id_journal, plano):
        fd, _identidade = _abrir_journal_handle(Path(str(arquivo) + '.stage'))
        try:
            dados = _conteudo_handle(fd)
            documento = json.loads(_dpapi(dados, False).decode('utf-8'))
            if not isinstance(documento, dict) or not isinstance(documento.get('conteudo'), str):
                raise TypeError('Registro de stage inválido.')
            registro = json.loads(documento['conteudo'])
            if not isinstance(registro, dict):
                raise TypeError('Registro de stage inválido.')
            id_stage = registro.get('id_stage')
            esperado = hashlib.sha256(json.dumps(plano, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
            if (
                plano.get('destino_ausente') is not True
                or not isinstance(plano.get('stage'), str)
                or not re.fullmatch(r'passe1-stage-[0-9a-f]{32}', plano['stage'])
                or registro.get('versao') != 1
                or registro.get('journal') != list(id_journal)
                or registro.get('stage') != plano.get('stage')
                or not isinstance(registro.get('pais_destino'), list)
                or len(registro['pais_destino']) != len(Path(plano['destino']).parts) - 1
                or registro['pais_destino'][:len(plano['pais_destino'])] != plano['pais_destino']
                or registro.get('hash_novo') != plano.get('hash_novo')
                or registro.get('hash_plano') != esperado
                or documento.get('caminho') != plano['destino']
                or documento.get('identidade') != id_stage
                or not isinstance(id_stage, list) or len(id_stage) != 3
                or any(not isinstance(v, str) or not v.isdecimal() for v in id_stage)
            ):
                raise ValueError('Registro de stage não corresponde ao journal.')
            yield plano, registro
        finally:
            os.close(fd)


@contextmanager
def _abrir_stage_fixo(journal, *, publicado=False):
    """Confere o arquivo registrado por handle relativo e mantém-no fixado."""
    if os.name != 'nt':
        raise RuntimeError('Movimentação Windows indisponível nesta plataforma.')
    import ctypes
    import msvcrt
    from ctypes import wintypes

    from passe1_engine.win_private import _Attributes, _Info, _Io, _Unicode

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Info)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll = ctypes.WinDLL('ntdll')
    ntdll.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(_Attributes), ctypes.POINTER(_Io), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    ntdll.NtCreateFile.restype = ctypes.c_long
    with _abrir_registro_stage(journal) as (plano, registro):
        destino = Path(plano['destino'])
        with _ancorar_journal(destino.parent):
            if snapshot_pais(destino)['pais'] != registro['pais_destino']:
                raise ValueError('Diretório do stage mudou após o registro.')
            pai = kernel.CreateFileW(str(destino.parent), 0x100084, 3, None, 3, 0x02200000, None)
            if pai == wintypes.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                info_pai = _Info()
                if not kernel.GetFileInformationByHandle(pai, ctypes.byref(info_pai)):
                    raise ctypes.WinError(ctypes.get_last_error())
                id_pai = [str(info_pai.volume), str(info_pai.index_high), str(info_pai.index_low)]
                if info_pai.attributes & 0x400 or not info_pai.attributes & 0x10 or id_pai != registro['pais_destino'][-1]:
                    raise ValueError('Diretório do stage mudou após o registro.')
                nome = destino.name if publicado else registro['stage']
                buffer = ctypes.create_unicode_buffer(nome)
                tamanho = len(nome.encode('utf-16-le'))
                palavra = _Unicode(tamanho, tamanho + 2, ctypes.cast(buffer, wintypes.LPWSTR))
                attrs = _Attributes(ctypes.sizeof(_Attributes), pai, ctypes.pointer(palavra), 0x40 | 0x1000, None, None)
                handle, ios = wintypes.HANDLE(), _Io()
                status = ntdll.NtCreateFile(ctypes.byref(handle), 0xC0110080, ctypes.byref(attrs), ctypes.byref(ios), None, 0, 0, 1, 0x00200060, None, 0)
                if status < 0:
                    raise OSError(f'NtCreateFile do stage falhou: 0x{status & 0xffffffff:08x}')
                try:
                    info = _Info()
                    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    identidade = [str(info.volume), str(info.index_high), str(info.index_low)]
                    if info.attributes & (0x400 | 0x10) or info.links != 1 or identidade != registro['id_stage']:
                        raise ValueError('Identidade do stage mudou.')
                    fd = msvcrt.open_osfhandle(handle.value, os.O_RDWR | os.O_BINARY)
                    handle = None
                    try:
                        if not hmac.compare_digest(hashlib.sha256(_conteudo_handle(fd)).hexdigest(), registro['hash_novo']):
                            raise ValueError('Conteúdo do stage mudou.')
                        if snapshot_pais(destino)['pais'] != registro['pais_destino']:
                            raise ValueError('Diretório do stage mudou durante a preparação.')
                        yield fd, pai, plano, registro
                    finally:
                        os.close(fd)
                finally:
                    if handle is not None:
                        kernel.CloseHandle(handle)
            finally:
                kernel.CloseHandle(pai)


@contextmanager
def _origem_fixa(plano, *, em_quarentena=False):
    """Fixa origem e pai relativos ao snapshot para destino novo."""
    if os.name != 'nt':
        raise RuntimeError('Movimentação Windows indisponível nesta plataforma.')
    import ctypes
    import msvcrt
    from ctypes import wintypes

    from passe1_engine.win_private import _Attributes, _Info, _Io, _Unicode

    plano = _validar_journal(plano)
    origem = Path(plano['origem'])
    if em_quarentena:
        nome = plano.get('quarentena')
        if not isinstance(nome, str) or not re.fullmatch(r'passe1-move-[0-9a-f]{32}', nome):
            raise ValueError('Nome de quarentena inválido no journal.')
        origem = origem.with_name(nome)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Info)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll = ctypes.WinDLL('ntdll')
    ntdll.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(_Attributes), ctypes.POINTER(_Io), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    ntdll.NtCreateFile.restype = ctypes.c_long
    with _ancorar_journal(origem.parent):
        if snapshot_pais(origem)['pais'] != plano['pais_origem']:
            raise ValueError('Diretório da origem mudou após aprovação.')
        pai = kernel.CreateFileW(str(origem.parent), 0x100084, 3, None, 3, 0x02200000, None)
        if pai == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info_pai = _Info()
            if not kernel.GetFileInformationByHandle(pai, ctypes.byref(info_pai)):
                raise ctypes.WinError(ctypes.get_last_error())
            id_pai = [str(info_pai.volume), str(info_pai.index_high), str(info_pai.index_low)]
            if info_pai.attributes & 0x400 or not info_pai.attributes & 0x10 or id_pai != plano['pais_origem'][-1]:
                raise ValueError('Diretório da origem mudou após aprovação.')
            buffer = ctypes.create_unicode_buffer(origem.name)
            tamanho = len(origem.name.encode('utf-16-le'))
            palavra = _Unicode(tamanho, tamanho + 2, ctypes.cast(buffer, wintypes.LPWSTR))
            attrs = _Attributes(ctypes.sizeof(_Attributes), pai, ctypes.pointer(palavra), 0x40 | 0x1000, None, None)
            handle, ios = wintypes.HANDLE(), _Io()
            status = ntdll.NtCreateFile(ctypes.byref(handle), 0xC0110080, ctypes.byref(attrs), ctypes.byref(ios), None, 0, 0, 1, 0x00200060, None, 0)
            if status < 0:
                raise OSError(f'NtCreateFile da origem falhou: 0x{status & 0xffffffff:08x}')
            try:
                info = _Info()
                if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                    raise ctypes.WinError(ctypes.get_last_error())
                identidade = [str(info.volume), str(info.index_high), str(info.index_low)]
                if info.attributes & (0x400 | 0x10) or info.links != 1 or identidade != plano['id_origem']:
                    raise ValueError('Identidade da origem mudou.')
                fd = msvcrt.open_osfhandle(handle.value, os.O_RDWR | os.O_BINARY)
                handle = None
                try:
                    if not hmac.compare_digest(hashlib.sha256(_conteudo_handle(fd)).hexdigest(), plano['hash_origem']):
                        raise ValueError('Conteúdo da origem mudou.')
                    if snapshot_pais(origem)['pais'] != plano['pais_origem']:
                        raise ValueError('Diretório da origem mudou durante preparação.')
                    yield fd, pai
                finally:
                    os.close(fd)
            finally:
                if handle is not None:
                    kernel.CloseHandle(handle)
        finally:
            kernel.CloseHandle(pai)


def _publicar_ausente_experimental(journal):
    """Experimento desconectado: quarentena origem e publica stage validado."""
    try:
        with _abrir_stage_fixo(journal) as (fd_stage, pai_destino, plano, registro), _origem_fixa(plano) as (fd_origem, pai_origem):
            quarentena = plano.get('quarentena')
            if not isinstance(quarentena, str) or not re.fullmatch(r'passe1-move-[0-9a-f]{32}', quarentena):
                raise ValueError('Nome de quarentena inválido no journal.')
            destino = Path(plano['destino'])
            if os.path.lexists(destino):
                raise ValueError('Destino surgiu após a aprovação.')
            _renomear_handle(fd_origem, pai_origem, quarentena)
            _renomear_handle(fd_stage, pai_destino, destino.name)
            return {'arquivo': registro['id_stage'], 'pais': registro['pais_destino']}
    except BaseException as exc:
        raise RuntimeError(f'Publicação interrompida; journal preservado: {str(journal)!r}') from exc


def _mover_ausente_experimental(origem, destino, anterior_origem, novo, identidade_origem, identidade_destino, journal_root):
    """Orquestra protótipo de destino novo; ainda desconectado do runtime."""
    _verificar_tamanho_revisavel(anterior_origem, novo)
    if not isinstance(identidade_origem, dict) or not isinstance(identidade_destino, dict):
        raise TypeError('Identidades aprovadas inválidas.')
    if identidade_destino.get('arquivo') is not None:
        raise ValueError('Identidade de destino ausente inválida.')
    plano = {
        'versao': 1, 'origem': os.path.abspath(origem), 'destino': os.path.abspath(destino),
        'id_origem': identidade_origem.get('arquivo'), 'id_destino': None,
        'pais_origem': identidade_origem.get('pais'), 'pais_destino': identidade_destino.get('pais'),
        'destino_ausente': True, 'conteudo_destino': None, 'hash_destino': None,
        'conteudo_origem': anterior_origem, 'hash_origem': _digest(anterior_origem),
        'conteudo_novo': novo, 'hash_novo': _digest(novo),
        'stage': 'passe1-stage-' + secrets.token_hex(16),
        'quarentena': 'passe1-move-' + secrets.token_hex(16),
    }
    _validar_journal(plano)
    journal = None
    try:
        with _origem_fixa(plano), _journal_fixo(journal_root, plano) as (journal, _):
            pass
        with _preparar_stage_registrado(journal) as (fd, _, _, _):
            _gravar_handle(fd, novo.encode('utf-8'))
        identidade = _publicar_ausente_experimental(journal)
        return journal, identidade
    except BaseException as exc:
        if journal is not None:
            raise RuntimeError(f'Movimentação interrompida; journal preservado: {str(journal)!r}') from exc
        raise


@contextmanager
def _revisao_ausente_fixa(journal):
    with _abrir_plano_fixo(journal) as (_, _, plano_inicial):
        plano_inicial = dict(plano_inicial)
    if plano_inicial.get('destino_ausente') is not True:
        raise ValueError('Journal não é de destino ausente.')
    origem, destino = Path(plano_inicial['origem']), Path(plano_inicial['destino'])
    quarentena, nome_stage = plano_inicial.get('quarentena'), plano_inicial.get('stage')
    if not isinstance(quarentena, str) or not re.fullmatch(r'passe1-move-[0-9a-f]{32}', quarentena):
        raise ValueError('Nome de quarentena inválido no journal.')
    if not isinstance(nome_stage, str) or not re.fullmatch(r'passe1-stage-[0-9a-f]{32}', nome_stage):
        raise ValueError('Nome de stage inválido no journal.')
    etapa = destino.with_name(nome_stage)
    presente, guardada = os.path.lexists(origem), os.path.lexists(origem.with_name(quarentena))
    preparado, publicado = os.path.lexists(etapa), os.path.lexists(destino)
    if presente == guardada or (preparado and publicado):
        raise ValueError('Arquivos de recuperação ausentes ou em conflito.')
    with ExitStack() as pilha:
        if preparado or publicado:
            fd_stage, _pai_destino, plano, registro = pilha.enter_context(_abrir_stage_fixo(journal, publicado=publicado))
            atual = _conteudo_handle(fd_stage)
        else:
            plano, registro = pilha.enter_context(_abrir_registro_stage(journal))
            fd_stage, atual = None, b''
        if plano != plano_inicial:
            raise ValueError('Journal mudou durante a revisão.')
        fd_origem, pai_origem = pilha.enter_context(_origem_fixa(plano, em_quarentena=guardada))
        if (os.path.lexists(origem), os.path.lexists(origem.with_name(quarentena)), os.path.lexists(etapa), os.path.lexists(destino)) != (presente, guardada, preparado, publicado):
            raise ValueError('Arquivos mudaram durante a revisão.')
        estado = {'registro': registro, 'origem_quarentena': guardada, 'stage': preparado, 'publicado': publicado}
        revisao = hashlib.sha256(json.dumps(estado, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        yield plano, fd_origem, pai_origem, fd_stage, atual, guardada, preparado, publicado, revisao


@contextmanager
def _revisao_stage_inicial_fixa(journal):
    """Revisa estágio sem ID durável, preservando-o para inspeção manual."""
    with _abrir_plano_fixo(journal) as (arquivo, identidade, plano):
        nome, quarentena = plano.get('stage'), plano.get('quarentena')
        if (plano.get('destino_ausente') is not True
                or not isinstance(nome, str) or not re.fullmatch(r'passe1-stage-[0-9a-f]{32}', nome)
                or not isinstance(quarentena, str) or not re.fullmatch(r'passe1-move-[0-9a-f]{32}', quarentena)):
            raise ValueError('Journal inicial de destino ausente inválido.')
        if os.path.lexists(str(arquivo) + '.stage'):
            raise ValueError('Registro de stage surgiu durante a revisão.')
        origem, destino = Path(plano['origem']), Path(plano['destino'])
        if os.path.lexists(destino) or os.path.lexists(origem.with_name(quarentena)):
            raise ValueError('Origem ou destino está em estado não reconhecido.')
        stage = destino.with_name(nome)
        with _origem_fixa(plano):
            presente = os.path.lexists(stage)
            estado = {
                'journal': list(identidade), 'plano': hashlib.sha256(json.dumps(plano, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest(),
                'stage_sem_registro': presente,
            }
            revisao = hashlib.sha256(json.dumps(estado, sort_keys=True).encode('utf-8')).hexdigest()
            yield plano, stage, presente, revisao


def _revisar_ausente_experimental(journal):
    if not os.path.lexists(str(journal) + '.stage'):
        with _revisao_stage_inicial_fixa(journal) as (plano, stage, presente, revisao):
            token = secrets.token_urlsafe(32)
            if len(_revisoes) >= 32:
                del _revisoes[next(iter(_revisoes))]
            _revisoes[token] = revisao
            return {'revisao': token, 'origem': plano['origem'], 'destino': plano['destino'],
                    'conteudo_origem': plano['conteudo_origem'], 'conteudo_destino_anterior': None,
                    'conteudo_destino_atual': b'', 'origem_em_quarentena': False,
                    'stage_preparado': presente, 'destino_publicado': False,
                    'stage_caminho': str(stage), 'requer_limpeza_manual': presente}
    with _revisao_ausente_fixa(journal) as (plano, _, _, _, atual, guardada, preparado, publicado, revisao):
        token = secrets.token_urlsafe(32)
        if len(_revisoes) >= 32:
            del _revisoes[next(iter(_revisoes))]
        _revisoes[token] = revisao
        return {'revisao': token, 'origem': plano['origem'], 'destino': plano['destino'],
                'conteudo_origem': plano['conteudo_origem'], 'conteudo_destino_anterior': None,
                'conteudo_destino_atual': atual, 'origem_em_quarentena': guardada,
                'stage_preparado': preparado, 'destino_publicado': publicado}


def _recuperar_ausente_experimental(journal, revisao, *, confirmado=False):
    if confirmado is not True:
        raise ValueError('Recuperação exige confirmação explícita.')
    if not isinstance(revisao, str):
        raise TypeError('Revisão inválida ou já utilizada.')
    esperada = _revisoes.pop(revisao, None)
    if esperada is None:
        raise ValueError('Revisão inválida ou já utilizada.')
    if not os.path.lexists(str(journal) + '.stage'):
        with _revisao_stage_inicial_fixa(journal) as (_, stage, presente, atual):
            if not hmac.compare_digest(esperada, atual):
                raise ValueError('Estado mudou desde a revisão; recuperação recusada.')
            return {'stage_preservado': presente, 'stage_caminho': str(stage) if presente else None}
    with _revisao_ausente_fixa(journal) as (plano, fd_origem, pai_origem, fd_stage, _, guardada, _, _, atual):
        if not hmac.compare_digest(esperada, atual):
            raise ValueError('Estado mudou desde a revisão; recuperação recusada.')
        if fd_stage is not None:
            _marcar_exclusao_journal(fd_stage)
        if guardada:
            _renomear_handle(fd_origem, pai_origem, Path(plano['origem']).name)
    return {'stage_preservado': False, 'stage_caminho': None}


def _mover_experimental(origem, destino, anterior_origem, anterior_destino, novo, identidade_origem, identidade_destino, journal_root):
    """Experimento desconectado: guarda origem e altera destino com journal.

    Não deve ser exposto ao usuário até a recuperação transacional, validação
    de interrupções e revisão do Windows estarem implementadas.
    """
    _verificar_tamanho_revisavel(anterior_origem, anterior_destino, novo)
    if not isinstance(identidade_origem, dict) or not isinstance(identidade_destino, dict):
        raise TypeError('Identidades aprovadas inválidas.')
    plano = {
        'versao': 1,
        'origem': os.path.abspath(origem), 'destino': os.path.abspath(destino),
        'id_origem': identidade_origem.get('arquivo'), 'id_destino': identidade_destino.get('arquivo'),
        'pais_origem': identidade_origem.get('pais'), 'pais_destino': identidade_destino.get('pais'),
        'quarentena': 'passe1-move-' + secrets.token_hex(16),
    }
    for nome, conteudo in (('origem', anterior_origem), ('destino', anterior_destino), ('novo', novo)):
        plano['conteudo_' + nome] = conteudo
        plano['hash_' + nome] = _digest(conteudo)
    _validar_journal(plano)
    journal = None
    try:
        with _par_fixo(origem, destino, plano) as (arquivos, diretorios), _journal_fixo(journal_root, plano) as (journal, _):
            _gravar_handle(arquivos['destino'], novo.encode('utf-8'))
            _renomear_handle(arquivos['origem'], diretorios['origem'], plano['quarentena'])
        return journal
    except BaseException as exc:
        if journal is not None:
            raise RuntimeError(f'Movimentação interrompida; journal preservado: {str(journal)!r}') from exc
        raise


@contextmanager
def _abrir_plano_fixo(journal):
    """Abre o journal experimental cifrado, fixando sua identidade durante uso."""
    arquivo = Path(os.path.abspath(journal))
    with _ancorar_journal(arquivo.parent):
        fd, identidade = _abrir_journal_handle(arquivo)
        try:
            partes, tamanho = [], 0
            while parte := os.read(fd, 65536):
                tamanho += len(parte)
                if tamanho > LIMITE_EDICAO * 4:
                    raise ValueError('Journal excede o limite de recuperação.')
                partes.append(parte)
            documento = json.loads(_dpapi(b''.join(partes), False).decode('utf-8'))
            plano = _validar_journal(json.loads(documento['conteudo']))
            if documento.get('caminho') != plano['origem'] or documento.get('identidade') != plano['id_origem']:
                raise ValueError('Identidade do journal inválida.')
            yield arquivo, identidade, plano
        finally:
            os.close(fd)


def _conteudo_handle(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    partes, tamanho = [], 0
    while parte := os.read(fd, 65536):
        tamanho += len(parte)
        if tamanho > LIMITE_EDICAO:
            raise ValueError('Conteúdo excede o limite de 64 MiB para movimentação.')
        partes.append(parte)
    os.lseek(fd, 0, os.SEEK_SET)
    return b''.join(partes)


@contextmanager
def _revisao_fixa(journal):
    with _abrir_plano_fixo(journal) as (arquivo, identidade, plano):
        nome = plano.get('quarentena')
        if not isinstance(nome, str) or not re.fullmatch(r'passe1-move-[0-9a-f]{32}', nome):
            raise ValueError('Nome de quarentena inválido no journal.')
        origem = Path(plano['origem'])
        presente = os.path.lexists(origem)
        em_quarentena = os.path.lexists(origem.with_name(nome))
        if presente == em_quarentena:
            raise ValueError('Origem e quarentena estão ausentes ou em conflito.')
        with _par_fixo(origem, plano['destino'], plano, em_quarentena=em_quarentena, permitir_parcial=True) as (arquivos, diretorios):
            atual = _conteudo_handle(arquivos['destino'])
            revisao = hashlib.sha256(json.dumps({
                'journal': list(identidade), 'plano': hashlib.sha256(json.dumps(plano, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest(),
                'destino': hashlib.sha256(atual).hexdigest(), 'quarentena': em_quarentena,
            }, sort_keys=True).encode('utf-8')).hexdigest()
            yield arquivo, plano, arquivos, diretorios, em_quarentena, revisao, atual


def _revisar_experimental(journal):
    """Fornece dados à prévia local; nenhum conteúdo é impresso pelo módulo."""
    with _revisao_fixa(journal) as (_, plano, _, _, em_quarentena, revisao, atual):
        token = secrets.token_urlsafe(32)
        if len(_revisoes) >= 32:
            del _revisoes[next(iter(_revisoes))]
        _revisoes[token] = revisao
        return {
            'revisao': token,
            'origem_em_quarentena': em_quarentena,
            'origem': plano['origem'], 'destino': plano['destino'],
            'conteudo_origem': plano['conteudo_origem'],
            'conteudo_destino_anterior': plano['conteudo_destino'],
            'conteudo_destino_atual': atual,
        }


def _recuperar_experimental(journal, revisao, *, confirmado=False):
    if confirmado is not True:
        raise ValueError('Recuperação exige confirmação explícita.')
    if not isinstance(revisao, str):
        raise TypeError('Revisão inválida ou já utilizada.')
    esperada = _revisoes.pop(revisao, None)
    if esperada is None:
        raise ValueError('Revisão inválida ou já utilizada.')
    with _revisao_fixa(journal) as (_, plano, arquivos, diretorios, em_quarentena, revisao_atual, _):
        if not hmac.compare_digest(esperada, revisao_atual):
            raise ValueError('Estado mudou desde a revisão; recuperação recusada.')
        _restaurar_conteudo(arquivos['destino'], plano['conteudo_destino'].encode('utf-8'), plano['conteudo_novo'].encode('utf-8'), confirmado=True)
        if em_quarentena:
            _renomear_handle(arquivos['origem'], diretorios['origem'], Path(plano['origem']).name)
