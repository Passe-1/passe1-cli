"""Operações de arquivo ancoradas em descritores, sem seguir symlinks.

A revalidação reduz, mas não elimina, a janela entre conferir a árvore e
publicar um nome: POSIX não oferece uma operação atômica para ambas as ações.
"""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
import secrets
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Versao:
    raiz: Path
    pais: tuple[tuple[int, int], ...]
    arquivo: tuple[int, int, int, int, int, int]
    digest: bytes | None


def _disponivel():
    funcoes = (os.open, os.mkdir, os.stat, os.unlink, os.rename, os.link)
    if (os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")
            or not all(f in os.supports_dir_fd for f in funcoes)):
        raise RuntimeError("Operações seguras de arquivo indisponíveis nesta plataforma.")


def _partes(raiz, caminho):
    _disponivel()
    projeto = Path(os.path.abspath(raiz))
    alvo = Path(caminho)
    if alvo.is_absolute():
        try:
            alvo = alvo.relative_to(projeto)
        except ValueError:
            raise ValueError("Caminho fora do projeto.") from None
    partes = alvo.parts
    if not partes or any(p in {"..", "."} for p in partes):
        raise ValueError("Caminho inválido ou fora do projeto.")
    return projeto, partes


@contextlib.contextmanager
def _pai(raiz, caminho, criar=False, esperados=None):
    projeto, partes = _partes(raiz, caminho)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fds = []
    try:
        fd = os.open(projeto.anchor, flags)
        fds.append(fd)
        for parte in projeto.parts[1:]:
            fd = os.open(parte, flags, dir_fd=fd)
            fds.append(fd)
        raiz_stat = os.fstat(fd)
        pais = [(raiz_stat.st_dev, raiz_stat.st_ino)]
        if esperados is not None and (not esperados or [str(v) for v in pais[0]] != esperados[0]):
            raise ValueError("Diretório raiz mudou após a aprovação.")
        for parte in partes[:-1]:
            indice = len(pais)
            if esperados is not None and indice >= len(esperados):
                if not criar:
                    raise ValueError("Diretório ausente na aprovação.")
                try:
                    os.mkdir(parte, 0o755, dir_fd=fd)
                except FileExistsError:
                    raise ValueError("Diretório apareceu após a aprovação.") from None
                novo = os.open(parte, flags, dir_fd=fd)
            else:
                try:
                    novo = os.open(parte, flags, dir_fd=fd)
                except FileNotFoundError:
                    if not criar or esperados is not None:
                        raise
                    try:
                        os.mkdir(parte, 0o755, dir_fd=fd)
                    except FileExistsError:
                        pass
                    novo = os.open(parte, flags, dir_fd=fd)
            fds.append(novo)
            fd = novo
            st = os.fstat(fd)
            pais.append((st.st_dev, st.st_ino))
            if esperados is not None and indice < len(esperados) and [str(st.st_dev), str(st.st_ino)] != esperados[indice]:
                raise ValueError("Diretório mudou após a aprovação.")
        yield fd, partes[-1], tuple(pais)
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _estado(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_mode)


def _validar_arquivo(st):
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise ValueError("Arquivo não é regular ou possui múltiplos links.")


def _abrir_arquivo(pai, nome):
    fd = os.open(nome, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pai)
    try:
        st = os.fstat(fd)
        _validar_arquivo(st)
        return fd, st
    except BaseException:
        os.close(fd)
        raise


def ler(raiz, caminho, limite=None):
    """Lê texto UTF-8 e retorna conteúdo e versão para revalidar uma edição."""
    if limite is not None and (not isinstance(limite, int) or limite < 0):
        raise ValueError("Limite inválido.")
    raiz_resolvida = Path(os.path.abspath(raiz))
    with _pai(raiz_resolvida, caminho) as (pai, nome, pais):
        fd, antes = _abrir_arquivo(pai, nome)
        try:
            with os.fdopen(os.dup(fd), "r", encoding="utf-8", newline="") as arquivo:
                texto = arquivo.read() if limite is None else arquivo.read(limite + 1)
            depois = os.fstat(fd)
            _validar_arquivo(depois)
            if _estado(antes) != _estado(depois):
                raise ValueError("Arquivo mudou durante a leitura.")
            truncado = limite is not None and len(texto) > limite
            digest = None if truncado else hashlib.sha256(texto.encode("utf-8")).digest()
            versao = Versao(raiz_resolvida, pais, _estado(depois), digest)
            return (texto[:limite] + "\n[arquivo truncado]" if truncado else texto), versao
        finally:
            os.close(fd)


def _copiar_metadados(origem, destino):
    st = os.fstat(origem)
    if sys.platform == "darwin":
        # COPYFILE_METADATA = ACL | STAT | XATTR, conforme copyfile.h da Apple.
        copia = ctypes.CDLL(None, use_errno=True).fcopyfile
        copia.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        copia.restype = ctypes.c_int
        if copia(origem, destino, None, 7) != 0:
            erro = ctypes.get_errno()
            raise OSError(erro, "Não foi possível preservar metadados do arquivo.")
    elif all(hasattr(os, nome) for nome in ("listxattr", "getxattr", "setxattr")):
        os.fchown(destino, st.st_uid, st.st_gid)
        os.fchmod(destino, stat.S_IMODE(st.st_mode))
        for nome in os.listxattr(origem):
            os.setxattr(destino, nome, os.getxattr(origem, nome))
    else:
        raise RuntimeError("Preservação de metadados indisponível nesta plataforma.")
    depois = os.fstat(destino)
    if (depois.st_uid, depois.st_gid, stat.S_IMODE(depois.st_mode), getattr(depois, "st_flags", 0)) != (
            st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode), getattr(st, "st_flags", 0)):
        raise ValueError("Não foi possível preservar os metadados do arquivo.")


def _gravar(fd, conteudo):
    dados = conteudo.encode("utf-8")
    while dados:
        n = os.write(fd, dados)
        if n <= 0:
            raise OSError("Não foi possível gravar o arquivo.")
        dados = dados[n:]
    os.fsync(fd)


def _remover_temporario(pai, nome, identidade):
    try:
        atual = os.stat(nome, dir_fd=pai, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (atual.st_dev, atual.st_ino) == identidade:
        os.unlink(nome, dir_fd=pai)


def criar(raiz, caminho, conteudo, pais_esperados=None):
    """Cria arquivo completo sem seguir links ou sobrescrever entrada existente."""
    raiz_resolvida = Path(os.path.abspath(raiz))
    with _pai(raiz_resolvida, caminho, criar=True, esperados=pais_esperados) as (pai, _nome, pais):
        tmp = ".passe1-" + secrets.token_hex(16)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666, dir_fd=pai)
        st = os.fstat(fd)
        identidade = (st.st_dev, st.st_ino)
        try:
            _gravar(fd, conteudo)
            with _pai(raiz_resolvida, caminho) as (pai_atual, nome_atual, pais_atuais):
                if Path(raiz).resolve(strict=True) != raiz_resolvida or pais_atuais != pais:
                    raise ValueError("Diretório mudou durante a criação.")
                atual = os.stat(tmp, dir_fd=pai_atual, follow_symlinks=False)
                if (atual.st_dev, atual.st_ino) != identidade or not stat.S_ISREG(atual.st_mode) or atual.st_nlink != 1:
                    raise ValueError("Arquivo temporário mudou durante a criação.")
                os.link(tmp, nome_atual, src_dir_fd=pai_atual, dst_dir_fd=pai_atual, follow_symlinks=False)
        finally:
            os.close(fd)
            _remover_temporario(pai, tmp, identidade)


def editar(raiz, caminho, versao, novo):
    """Substitui arquivo só se conteúdo e identidades coincidirem com a leitura."""
    if not isinstance(versao, Versao) or versao.digest is None:
        raise ValueError("A leitura precisa estar completa antes da edição.")
    if Path(raiz).resolve(strict=True) != versao.raiz:
        raise ValueError("Raiz mudou durante a aprovação.")
    with _pai(versao.raiz, caminho) as (pai, nome, pais):
        if pais != versao.pais:
            raise ValueError("Diretório mudou durante a aprovação.")
        original, st = _abrir_arquivo(pai, nome)
        tmp = ".passe1-" + secrets.token_hex(16)
        identidade_tmp = None
        try:
            if _estado(st) != versao.arquivo:
                raise ValueError("Arquivo mudou durante a aprovação.")
            with os.fdopen(os.dup(original), "r", encoding="utf-8", newline="") as f:
                conteudo = f.read()
            if hashlib.sha256(conteudo.encode("utf-8")).digest() != versao.digest:
                raise ValueError("Arquivo mudou durante a aprovação.")
            destino = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=pai)
            st_tmp = os.fstat(destino)
            identidade_tmp = (st_tmp.st_dev, st_tmp.st_ino)
            try:
                _gravar(destino, novo)
                _copiar_metadados(original, destino)
                os.utime(destino, ns=(st.st_atime_ns, max(time.time_ns(), st.st_mtime_ns + 1)))
                os.fsync(destino)
            finally:
                os.close(destino)
            with _pai(versao.raiz, caminho) as (pai_atual, nome_atual, pais_atuais):
                if Path(raiz).resolve(strict=True) != versao.raiz:
                    raise ValueError("Raiz mudou durante a aprovação.")
                st_atual = os.stat(nome_atual, dir_fd=pai_atual, follow_symlinks=False)
                _validar_arquivo(st_atual)
                if pais_atuais != versao.pais or _estado(st_atual) != versao.arquivo:
                    raise ValueError("Arquivo ou diretório mudou durante a aprovação.")
                atual_tmp = os.stat(tmp, dir_fd=pai, follow_symlinks=False)
                _validar_arquivo(atual_tmp)
                if (atual_tmp.st_dev, atual_tmp.st_ino) != identidade_tmp:
                    raise ValueError("Arquivo temporário mudou durante a edição.")
                os.replace(tmp, nome_atual, src_dir_fd=pai, dst_dir_fd=pai_atual)
        finally:
            os.close(original)
            if identidade_tmp is not None:
                _remover_temporario(pai, tmp, identidade_tmp)
