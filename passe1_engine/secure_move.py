"""Movimentação POSIX recuperável com quarentena e renames exclusivos.

A quarentena é mantida porque POSIX não oferece unlink condicional por inode.
A validação após rename não é atômica contra outro processo do mesmo usuário.
"""
from __future__ import annotations

import ctypes
import difflib
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path

import arquivos_seguros
import terminal

LIMITE_MOVE_REVISAVEL = 1024 * 1024

_REVISOES = {}


def _exclusivo(origem_fd, origem, destino_fd, destino):
    """Renomeia sem substituir destino; falha fechado se a API não existir."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        funcao = getattr(libc, 'renameatx_np', None)
        flags = 4  # RENAME_EXCL
    elif sys.platform.startswith('linux'):
        funcao = getattr(libc, 'renameat2', None)
        flags = 1  # RENAME_NOREPLACE
    else:
        raise RuntimeError('Move transacional indisponível nesta plataforma.')
    if funcao is None:
        raise RuntimeError('Rename exclusivo indisponível nesta plataforma.')
    funcao.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    funcao.restype = ctypes.c_int
    if funcao(origem_fd, os.fsencode(origem), destino_fd, os.fsencode(destino), flags) != 0:
        erro = ctypes.get_errno()
        raise OSError(erro, os.strerror(erro))


def _id(st):
    return [str(st.st_dev), str(st.st_ino)]


def _verificar(caminho, conteudo, identidade):
    texto, versao = arquivos_seguros.ler(Path(caminho.anchor), caminho)
    if texto != conteudo or [str(x) for x in versao.arquivo] != identidade.get('arquivo') or [[str(x) for x in p] for p in versao.pais] != identidade.get('pais'):
        raise ValueError('Arquivo ou diretório mudou após a aprovação.')
    return versao


def _estat(pai, nome):
    try:
        st = os.stat(nome, dir_fd=pai, follow_symlinks=False)
    except FileNotFoundError:
        return None
    arquivos_seguros._validar_arquivo(st)
    return st



def _privado(fd):
    """Recusa ACLs estendidas no macOS, onde mode 0700 não limita ACLs."""
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError('Diretório ou arquivo de journal não é privado.')
    if sys.platform == 'darwin':
        libc = ctypes.CDLL(None, use_errno=True)
        obter = getattr(libc, 'acl_get_fd_np', None)
        liberar = getattr(libc, 'acl_free', None)
        if obter is None or liberar is None:
            raise RuntimeError('Não foi possível verificar ACL do journal.')
        obter.argtypes = [ctypes.c_int, ctypes.c_int]
        obter.restype = ctypes.c_void_p
        liberar.argtypes = [ctypes.c_void_p]
        liberar.restype = ctypes.c_int
        ctypes.set_errno(0)
        acl = obter(fd, 0x100)  # ACL_TYPE_EXTENDED
        if acl:
            liberar(acl)
            raise ValueError('Journal possui ACL estendida; use diretório privado sem ACL.')
        if ctypes.get_errno() != errno.ENOENT:
            raise OSError(ctypes.get_errno(), 'Não foi possível verificar ACL do journal.')


def _quarentena(pai, nome, esperado=None, *, criar=False):
    if criar:
        os.mkdir(nome, 0o700, dir_fd=pai)
    fd = os.open(nome, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pai)
    try:
        _privado(fd)
        atual = _id(os.fstat(fd))
        if esperado is not None and atual != esperado:
            raise ValueError('Diretório de quarentena mudou.')
        return fd, atual
    except BaseException:
        os.close(fd)
        raise


def _ler_guardado(pai, nome):
    fd, antes = arquivos_seguros._abrir_arquivo(pai, nome)
    try:
        with os.fdopen(os.dup(fd), 'r', encoding='utf-8', newline='') as arquivo:
            texto = arquivo.read()
        depois = os.fstat(fd)
        arquivos_seguros._validar_arquivo(depois)
        if arquivos_seguros._estado(antes) != arquivos_seguros._estado(depois):
            raise ValueError('Arquivo em quarentena mudou durante a leitura.')
        return texto, _id(depois)
    finally:
        os.close(fd)

def _manifesto(fd, dados):
    temporario = '.manifesto-' + secrets.token_hex(16)
    arquivo = os.open(temporario, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        _privado(arquivo)
        arquivos_seguros._gravar(arquivo, json.dumps(dados, ensure_ascii=False))
    finally:
        os.close(arquivo)
    os.replace(temporario, 'manifesto.json', src_dir_fd=fd, dst_dir_fd=fd)
    os.fsync(fd)


def _abrir_journal(raiz, criar=False):
    """Abre a raiz sem seguir links; o chamador fecha o contexto."""
    return arquivos_seguros._pai(Path(raiz.anchor), raiz / '.entrada', criar=criar)



def preparar_raiz(journal_root):
    """Cria ou valida diretório privado sem seguir links."""
    raiz = Path(os.path.abspath(journal_root))
    with arquivos_seguros._pai(Path(raiz.anchor), raiz) as (pai, nome, _):
        try:
            os.mkdir(nome, 0o700, dir_fd=pai)
            os.fsync(pai)
        except FileExistsError:
            pass
        fd = os.open(nome, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pai)
        try:
            _privado(fd)
        finally:
            os.close(fd)
    return raiz

def mover(origem, destino, anterior_origem, anterior_destino, novo, identidade_origem, identidade_destino, journal_root, *, retornar_estado=False):
    """Move com journal recuperável, mantendo cópias de quarentena após sucesso.

    Retorna o caminho do journal. A remoção definitiva das quarentenas exige
    operação separada, pois unlink não oferece comparação atômica de inode.
    """
    arquivos_seguros._disponivel()
    origem, destino, journal_root = (Path(os.path.abspath(p)) for p in (origem, destino, journal_root))
    if origem == destino or not isinstance(anterior_origem, str) or not (anterior_destino is None or isinstance(anterior_destino, str)) or not isinstance(novo, str):
        raise ValueError('Origem, destino ou conteúdo inválido.')
    if sum(len(texto.encode('utf-8')) for texto in (anterior_origem, anterior_destino, novo) if texto is not None) > LIMITE_MOVE_REVISAVEL:
        raise ValueError('Move exige recuperação revisável; conteúdo total excede 1 MiB. Nenhum arquivo foi alterado.')
    vo = _verificar(origem, anterior_origem, identidade_origem)
    vd = _verificar(destino, anterior_destino, identidade_destino) if anterior_destino is not None else None
    if anterior_destino is None and (not isinstance(identidade_destino, dict) or not isinstance(identidade_destino.get('pais'), list) or not identidade_destino['pais']):
        raise ValueError('Identidade dos diretórios do destino é obrigatória.')
    token = secrets.token_hex(16)
    hold_o = '.passe1-move-' + token + '-origem'
    hold_d = '.passe1-move-' + token + '-destino'
    with _abrir_journal(journal_root) as (jroot, _, _):
        _privado(jroot)
        nome_journal = '.passe1-journal-' + token
        os.mkdir(nome_journal, 0o700, dir_fd=jroot)
        jfd = os.open(nome_journal, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=jroot)
        os.fsync(jroot)
        try:
            _privado(jfd)
            with arquivos_seguros._pai(Path(origem.anchor), origem) as (of, on, op), arquivos_seguros._pai(
                Path(destino.anchor), destino, criar=vd is None,
                esperados=identidade_destino['pais'] if vd is None else None,
            ) as (df, dn, dp):
                if op != vo.pais or (vd is not None and dp != vd.pais):
                    raise ValueError('Diretório mudou após a aprovação.')
                o, d = _estat(of, on), _estat(df, dn)
                if o is None or arquivos_seguros._estado(o) != vo.arquivo:
                    raise ValueError('Origem mudou após a aprovação.')
                if (vd is None and d is not None) or (vd is not None and (d is None or arquivos_seguros._estado(d) != vd.arquivo)):
                    raise ValueError('Destino mudou após a aprovação.')
                dados = {'versao': 2, 'origem': str(origem), 'destino': str(destino), 'hold_origem': hold_o, 'hold_destino': hold_d, 'id_qo': None, 'id_qd': None, 'id_origem': _id(o), 'id_destino': _id(d) if d else None, 'destino_ausente': vd is None, 'id_novo': None, 'hash_origem': hashlib.sha256(anterior_origem.encode()).hexdigest(), 'hash_destino': hashlib.sha256(anterior_destino.encode()).hexdigest() if anterior_destino is not None else None, 'hash_novo': hashlib.sha256(novo.encode()).hexdigest(), 'pais_origem': identidade_origem['pais'], 'pais_destino': [[str(valor) for valor in item] for item in dp], 'fase': 'preparando'}
                _manifesto(jfd, dados)
                qof = qdf = None
                try:
                    qof, dados['id_qo'] = _quarentena(of, hold_o, criar=True)
                    qdf, dados['id_qd'] = _quarentena(df, hold_d, criar=True)
                    os.fsync(of)
                    os.fsync(df)
                    _manifesto(jfd, dados)
                    staged = os.open('novo', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600 if vd is not None else 0o666, dir_fd=qdf)
                    try:
                        arquivos_seguros._gravar(staged, novo)
                        if vd is not None:
                            original_d, _ = arquivos_seguros._abrir_arquivo(df, dn)
                            try:
                                arquivos_seguros._copiar_metadados(original_d, staged)
                            finally:
                                os.close(original_d)
                        os.fsync(staged)
                        dados['id_novo'] = _id(os.fstat(staged))
                    finally:
                        os.close(staged)
                    os.fsync(qdf)
                    dados['fase'] = 'preparado'
                    _manifesto(jfd, dados)
                    _exclusivo(of, on, qof, 'origem')
                    os.fsync(of)
                    os.fsync(qof)
                    dados['fase'] = 'origem_guardada'
                    _manifesto(jfd, dados)
                    guardado, id_guardado = _ler_guardado(qof, 'origem')
                    if id_guardado != dados['id_origem'] or guardado != anterior_origem:
                        raise ValueError('Origem mudou durante a movimentação.')
                    if vd is not None:
                        _exclusivo(df, dn, qdf, 'destino')
                        os.fsync(df)
                        os.fsync(qdf)
                        dados['fase'] = 'destino_guardado'
                        _manifesto(jfd, dados)
                        guardado, id_guardado = _ler_guardado(qdf, 'destino')
                        if id_guardado != dados['id_destino'] or guardado != anterior_destino:
                            raise ValueError('Destino mudou durante a movimentação.')
                    _exclusivo(qdf, 'novo', df, dn)
                    os.fsync(qdf)
                    os.fsync(df)
                    publicado = _estat(df, dn)
                    publicado_texto, publicado_versao = arquivos_seguros.ler(Path(destino.anchor), destino)
                    if publicado is None or _id(publicado) != dados['id_novo'] or [str(v) for v in publicado_versao.arquivo[:2]] != dados['id_novo'] or publicado_texto != novo:
                        raise ValueError('Arquivo publicado mudou durante a movimentação.')
                    dados['fase'] = 'aplicado'
                    _manifesto(jfd, dados)
                    caminho_journal = journal_root / nome_journal
                    if retornar_estado:
                        return {'journal': str(caminho_journal), 'pais': [[str(valor) for valor in item] for item in dp]}
                    return caminho_journal
                finally:
                    if qdf is not None:
                        os.close(qdf)
                    if qof is not None:
                        os.close(qof)
        except BaseException as exc:
            caminho = terminal.seguro(journal_root / nome_journal).replace('\n', r'\n')
            raise RuntimeError(f'Movimentação interrompida; journal preservado em {caminho}.') from exc
        finally:
            os.close(jfd)


def _inspecionar(journal, *, executar=False, revisao=None, incluir_previa=False):
    """Valida estado e, somente após confirmação, executa os renames."""
    journal = Path(os.path.abspath(journal))
    if not re.fullmatch(r'\.passe1-journal-[0-9a-f]{32}', journal.name):
        raise ValueError('Journal inválido.')
    with _abrir_journal(journal.parent) as (jroot, _, _):
        _privado(jroot)
        jfd = os.open(journal.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=jroot)
        try:
            _privado(jfd)
            id_journal = _id(os.fstat(jfd))
            fd = os.open('manifesto.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=jfd)
            try:
                info_manifesto = os.fstat(fd)
                arquivos_seguros._validar_arquivo(info_manifesto)
                _privado(fd)
                with os.fdopen(os.dup(fd), 'rb') as arquivo:
                    bruto = arquivo.read(1024 * 1024 + 1)
                if len(bruto) > 1024 * 1024 or arquivos_seguros._estado(os.fstat(fd)) != arquivos_seguros._estado(info_manifesto):
                    raise ValueError('Journal excessivo ou alterado durante leitura.')
                dados = json.loads(bruto)
            finally:
                os.close(fd)
        finally:
            os.close(jfd)
    if not isinstance(dados, dict) or dados.get('versao') != 2 or dados.get('fase') not in {'preparando', 'preparado', 'origem_guardada', 'destino_guardado', 'aplicado'}:
        raise ValueError('Journal inválido.')
    origem, destino = Path(dados['origem']), Path(dados['destino'])
    if not origem.is_absolute() or not destino.is_absolute():
        raise ValueError('Journal inválido.')
    destino_ausente = dados.get('destino_ausente', False)
    if not isinstance(destino_ausente, bool) or (destino_ausente and (dados.get('id_destino') is not None or dados.get('hash_destino') is not None)):
        raise ValueError('Journal inválido.')
    token = journal.name.removeprefix('.passe1-journal-')
    if dados.get('hold_origem') != '.passe1-move-' + token + '-origem' or dados.get('hold_destino') != '.passe1-move-' + token + '-destino':
        raise ValueError('Journal inválido.')
    base = {'journal': str(journal), 'origem': str(origem), 'destino': str(destino), 'fase': dados['fase'],
            'quarentena_origem': str(origem.parent / dados['hold_origem']),
            'quarentena_destino': str(destino.parent / dados['hold_destino']),
            'aviso': 'O journal não é autenticado contra processo malicioso do mesmo usuário.',
            'manifesto_sha256': hashlib.sha256(bruto).hexdigest(), 'id_journal': id_journal,
            'id_manifesto': [str(v) for v in arquivos_seguros._estado(info_manifesto)]}
    with arquivos_seguros._pai(Path(origem.anchor), origem) as (of, on, op), arquivos_seguros._pai(Path(destino.anchor), destino) as (df, dn, dp):
        if [[str(x) for x in item] for item in op] != dados['pais_origem'] or [[str(x) for x in item] for item in dp] != dados['pais_destino']:
            raise ValueError('Diretório mudou; recuperação recusada.')
        if dados['id_qo'] is None or dados['id_qd'] is None:
            if dados.get('fase') == 'preparando':
                originais = [(origem, dados['hash_origem'], dados['id_origem'])]
                if destino_ausente:
                    if _estat(df, dn) is not None:
                        raise ValueError('Destino foi criado por terceiro; recuperação recusada.')
                else:
                    originais.append((destino, dados['hash_destino'], dados['id_destino']))
                for caminho, conteudo_hash, id_esperado in originais:
                    texto, versao = arquivos_seguros.ler(Path(caminho.anchor), caminho)
                    if [str(v) for v in versao.arquivo[:2]] != id_esperado or hashlib.sha256(texto.encode()).hexdigest() != conteudo_hash:
                        raise ValueError('Arquivo mudou; recuperação recusada.')
                base.update({'acoes': [], 'arquivos_residuais_possiveis': True})
                codigo = _codigo_revisao(base)
                if executar and revisao != codigo:
                    raise ValueError('Estado mudou após a revisão; revise novamente.')
                resultado = {**base, 'revisao': codigo}
                if incluir_previa:
                    resultado['previa'] = 'Nenhum arquivo original precisa ser restaurado; há resíduos possíveis na quarentena.'
                return resultado
            raise ValueError('Identidade da quarentena ausente.')
        qof, _ = _quarentena(of, dados['hold_origem'], dados['id_qo'])
        try:
            qdf, _ = _quarentena(df, dados['hold_destino'], dados['id_qd'])
            try:
                atual = _estat(df, dn)
                texto_destino_atual = ''
                texto_destino_guardado = None
                texto_origem_guardado = None
                hold_d = _estat(qdf, 'destino')
                hold_o = _estat(qof, 'origem')
                if hold_d is not None and destino_ausente:
                    raise ValueError('Destino inesperado na quarentena; recuperação recusada.')
                if hold_d is not None:
                    conteudo, identidade = _ler_guardado(qdf, 'destino')
                    if identidade != dados['id_destino'] or hashlib.sha256(conteudo.encode()).hexdigest() != dados['hash_destino']:
                        raise ValueError('Destino guardado foi substituído; recuperação recusada.')
                    texto_destino_guardado = conteudo
                if hold_o is not None:
                    conteudo, identidade = _ler_guardado(qof, 'origem')
                    if identidade != dados['id_origem'] or hashlib.sha256(conteudo.encode()).hexdigest() != dados['hash_origem']:
                        raise ValueError('Origem guardada foi substituída; recuperação recusada.')
                    texto_origem_guardado = conteudo
                if hold_o is not None and _estat(of, on) is not None:
                    raise ValueError('Origem foi alterada; recuperação recusada.')
                if (hold_d is not None or destino_ausente) and atual is not None:
                    texto, versao = arquivos_seguros.ler(Path(destino.anchor), destino)
                    if _id(atual) != dados['id_novo'] or [str(v) for v in versao.arquivo[:2]] != dados['id_novo'] or hashlib.sha256(texto.encode()).hexdigest() != dados['hash_novo']:
                        raise ValueError('Destino foi alterado; recuperação recusada.')
                    texto_destino_atual = texto
                    if destino_ausente:
                        texto_destino_guardado = ''
                original = _estat(of, on)
                if hold_o is None:
                    if original is None:
                        raise ValueError('Origem ausente; recuperação recusada.')
                    texto, versao = arquivos_seguros.ler(Path(origem.anchor), origem)
                    if _id(original) != dados['id_origem'] or [str(v) for v in versao.arquivo[:2]] != dados['id_origem'] or hashlib.sha256(texto.encode()).hexdigest() != dados['hash_origem']:
                        raise ValueError('Origem mudou; recuperação recusada.')
                if hold_d is None and not destino_ausente:
                    if atual is None:
                        raise ValueError('Destino ausente; recuperação recusada.')
                    texto, versao = arquivos_seguros.ler(Path(destino.anchor), destino)
                    if _id(atual) != dados['id_destino'] or [str(v) for v in versao.arquivo[:2]] != dados['id_destino'] or hashlib.sha256(texto.encode()).hexdigest() != dados['hash_destino']:
                        raise ValueError('Destino mudou; recuperação recusada.')
                acoes = []
                if (hold_d is not None or destino_ausente) and atual is not None:
                    acoes.append({'acao': 'guardar_resultado', 'origem': str(destino), 'destino': str(destino.parent / dados['hold_destino'] / 'novo')})
                if hold_d is not None:
                    acoes.append({'acao': 'restaurar_destino', 'origem': str(destino.parent / dados['hold_destino'] / 'destino'), 'destino': str(destino)})
                if hold_o is not None:
                    acoes.append({'acao': 'restaurar_origem', 'origem': str(origem.parent / dados['hold_origem'] / 'origem'), 'destino': str(origem)})
                base.update({'acoes': acoes, 'destino_ausente': destino_ausente, 'id_qo': dados['id_qo'], 'id_qd': dados['id_qd'],
                             'estado_origem': [str(v) for v in arquivos_seguros._estado(original)] if original else None,
                             'estado_destino': [str(v) for v in arquivos_seguros._estado(atual)] if atual else None,
                             'estado_backup_origem': [str(v) for v in arquivos_seguros._estado(hold_o)] if hold_o else None,
                             'estado_backup_destino': [str(v) for v in arquivos_seguros._estado(hold_d)] if hold_d else None})
                codigo = _codigo_revisao(base)
                if not executar:
                    resultado = {**base, 'revisao': codigo}
                    if incluir_previa:
                        resultado['previa'] = _previa_local(origem, destino, texto_origem_guardado, texto_destino_guardado, texto_destino_atual)
                    return resultado
                if revisao != codigo:
                    raise ValueError('Estado mudou após a revisão; revise novamente.')
                if (hold_d is not None or destino_ausente) and atual is not None:
                    _exclusivo(df, dn, qdf, 'novo')
                    _confirmar_movido(qdf, 'novo', dados['id_novo'], dados['hash_novo'], journal)
                    os.fsync(qdf)
                    os.fsync(df)
                if hold_d is not None:
                    _exclusivo(qdf, 'destino', df, dn)
                    _confirmar_movido(df, dn, dados['id_destino'], dados['hash_destino'], journal, quarentena=qdf)
                    os.fsync(qdf)
                    os.fsync(df)
                if hold_o is not None:
                    _exclusivo(qof, 'origem', of, on)
                    _confirmar_movido(of, on, dados['id_origem'], dados['hash_origem'], journal, quarentena=qof)
                    os.fsync(qof)
                    os.fsync(of)
            finally:
                os.close(qdf)
        finally:
            os.close(qof)
    return {**base, 'revisao': codigo}



def _confirmar_movido(pai, nome, identidade_esperada, hash_esperado, journal, *, quarentena=None):
    try:
        texto, identidade = _ler_guardado(pai, nome)
        if identidade != identidade_esperada or hashlib.sha256(texto.encode()).hexdigest() != hash_esperado:
            raise ValueError('Identidade ou conteúdo divergente.')
    except (OSError, ValueError, UnicodeError) as exc:
        detalhe = 'Não foi possível isolar a entrada inesperada.'
        if quarentena is not None:
            try:
                antes = os.stat(nome, dir_fd=pai, follow_symlinks=False)
                incidente = 'incidente-' + secrets.token_hex(16)
                _exclusivo(pai, nome, quarentena, incidente)
                depois = os.stat(incidente, dir_fd=quarentena, follow_symlinks=False)
                os.fsync(pai)
                os.fsync(quarentena)
                if (antes.st_dev, antes.st_ino, antes.st_mode) == (depois.st_dev, depois.st_ino, depois.st_mode):
                    detalhe = 'Entrada inesperada movida para quarentena; confira o journal.'
                else:
                    detalhe = 'Uma entrada diferente foi movida; confira o journal e a quarentena.'
            except (OSError, ValueError, RuntimeError):
                pass
        caminho = terminal.seguro(journal).replace('\n', r'\n')
        raise RuntimeError(f'Estado incerto após movimentação. {detalhe} Journal preservado em {caminho}.') from exc


def _previa_local(origem, destino, backup_origem, backup_destino, destino_atual):
    """Gera diff somente para apresentação local; não é retornado pela API JSON."""
    textos = [valor for valor in (backup_origem, backup_destino, destino_atual) if valor is not None]
    if sum(len(valor.encode('utf-8')) for valor in textos) > 1024 * 1024:
        raise ValueError('Recuperação exige revisão integral; conteúdo excede o limite de 1 MiB.')
    secoes = []
    for caminho, anterior, restaurado in ((origem, '', backup_origem), (destino, destino_atual, backup_destino)):
        if restaurado is None:
            continue
        nome = terminal.seguro(caminho).replace('\n', r'\n')
        diff = ''.join(difflib.unified_diff(anterior.splitlines(keepends=True), restaurado.splitlines(keepends=True), fromfile='atual: ' + nome, tofile='restaurado: ' + nome))
        secoes.append(terminal.seguro(diff or '(sem alterações de conteúdo)\n'))
    return '\n'.join(secoes) if secoes else 'Nenhum arquivo original precisa ser restaurado.'

def _codigo_revisao(plano):
    return hashlib.sha256(json.dumps(plano, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def revisar(journal):
    """Retorna somente metadados, sem conteúdo nem autorização de restauração."""
    resultado = _inspecionar(journal)
    resultado.pop('revisao', None)
    return resultado


def revisar_local(journal):
    """Gera prévia sanitizada destinada apenas ao terminal local do usuário."""
    resultado = _inspecionar(journal, incluir_previa=True)
    estado = resultado['revisao']
    caminho = resultado['journal']
    for anterior, dados in list(_REVISOES.items()):
        if dados[0] == caminho:
            del _REVISOES[anterior]
    while len(_REVISOES) >= 32:
        del _REVISOES[next(iter(_REVISOES))]
    token = secrets.token_hex(32)
    _REVISOES[token] = (caminho, estado)
    saida = {**resultado, 'revisao': token}
    for chave in ('journal', 'origem', 'destino', 'quarentena_origem', 'quarentena_destino'):
        saida[chave] = terminal.seguro(saida[chave]).replace('\n', r'\n')
    saida['acoes'] = [
        {chave: terminal.seguro(valor).replace('\n', r'\n') for chave, valor in item.items()}
        for item in saida['acoes']
    ]
    return saida


def recuperar(journal, revisao, *, confirmado=False):
    """Restaura somente o estado revisado localmente e confirmado."""
    if confirmado is not True or not isinstance(revisao, str):
        raise ValueError('Revise os caminhos e confirme explicitamente a recuperação.')
    dados = _REVISOES.pop(revisao, None)
    if dados is None or dados[0] != str(Path(os.path.abspath(journal))):
        raise ValueError('Revisão local ausente ou expirada; revise novamente.')
    return _inspecionar(journal, executar=True, revisao=dados[1])


def _main():
    import sys
    try:
        entrada = json.load(sys.stdin)
        if not isinstance(entrada, dict):
            raise TypeError('Operação inválida.')
        if entrada.get('operacao') == 'revisar':
            json.dump(revisar(entrada['journal']), sys.stdout)
            return 0
        if entrada.get('operacao') == 'recuperar':
            raise ValueError('Recuperação disponível somente no comando local com revisão e confirmação.')
        if entrada.get('operacao') == 'mover':
            if os.name == 'nt':
                from passe1_engine import win_move
                if entrada['anterior_destino'] is None:
                    journal, identidade = win_move._mover_ausente_experimental(
                        entrada['origem'], entrada['destino'], entrada['anterior_origem'], entrada['novo'],
                        entrada['identidade_origem'], entrada['identidade_destino'], entrada['journal_root'],
                    )
                    resultado = {'journal': str(journal), 'pais': identidade['pais']}
                else:
                    journal = win_move._mover_experimental(
                        entrada['origem'], entrada['destino'], entrada['anterior_origem'], entrada['anterior_destino'],
                        entrada['novo'], entrada['identidade_origem'], entrada['identidade_destino'], entrada['journal_root'],
                    )
                    resultado = {'journal': str(journal)}
            else:
                preparar_raiz(entrada['journal_root'])
                resultado = mover(
                    entrada['origem'], entrada['destino'], entrada['anterior_origem'],
                    entrada['anterior_destino'], entrada['novo'], entrada['identidade_origem'],
                    entrada['identidade_destino'], entrada['journal_root'], retornar_estado=True,
                )
        else:
            raise ValueError('Operação inválida.')
        estado = resultado if isinstance(resultado, dict) else {'journal': str(resultado)}
        exibicao = terminal.seguro(str(estado['journal'])).replace('\n', r'\n')
        json.dump({**estado, 'journal_display': exibicao, 'quarentena_mantida': True}, sys.stdout)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        detalhe = terminal.seguro(str(exc)).replace('\n', r'\n')
        print(f'Movimentação segura recusada: {detalhe}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
