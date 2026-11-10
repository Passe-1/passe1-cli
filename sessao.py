"""Persistência opcional de conversas do agente, restrita à conta e ao projeto."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows não oferece flock.
    fcntl = None


class Sessao:
    def __init__(self, diretorio, nome, raiz, base, modelo, chave):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", nome):
            raise ValueError("Nome de sessão inválido: use letras, números, hífen ou sublinhado.")
        self.diretorio = Path(diretorio) / "sessions"
        self.arquivo = self.diretorio / (nome + ".json")
        self.identidade = {"projeto": str(raiz), "gateway": hashlib.sha256(base.rstrip("/").encode()).hexdigest(), "modelo": modelo,
                           "conta": hashlib.sha256(chave.encode()).hexdigest()}
        self._lock = None

    def __enter__(self):
        if fcntl is None:
            raise ValueError("Sessões persistentes ainda não são suportadas neste sistema.")
        self.diretorio.mkdir(mode=0o700, parents=True, exist_ok=True)
        diretorio_fd = os.open(self.diretorio, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchmod(diretorio_fd, 0o700)
            fd = os.open(self.arquivo.with_suffix(".lock").name,
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=diretorio_fd)
        finally:
            os.close(diretorio_fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            os.close(fd)
            raise ValueError("Arquivo de bloqueio de sessão inválido ou sem privacidade.")
        self._lock = os.fdopen(fd, "r+")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock.close()
            raise ValueError("Esta sessão já está aberta em outro processo.") from exc
        return self

    def __exit__(self, *_args):
        fcntl.flock(self._lock, fcntl.LOCK_UN)
        self._lock.close()

    def carregar(self):
        try:
            with self.arquivo.open(encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise ValueError("Não foi possível ler a sessão salva.") from exc
        if not isinstance(dados, dict) or dados.get("identidade") != self.identidade:
            raise ValueError("Sessão pertence a outro projeto, gateway, modelo ou chave.")
        mensagens, conversa_id = dados.get("mensagens"), dados.get("conversa_id")
        if not isinstance(mensagens, list) or not isinstance(conversa_id, str) or not re.fullmatch(r"[0-9a-f]{32}", conversa_id):
            raise ValueError("Sessão salva está corrompida.")
        if any(not isinstance(m, dict) or m.get("role") not in {"user", "assistant", "tool"} for m in mensagens):
            raise ValueError("Sessão salva está corrompida.")
        return mensagens, conversa_id

    def salvar(self, mensagens, conversa_id):
        dados = {"identidade": self.identidade, "mensagens": mensagens, "conversa_id": conversa_id}
        fd, caminho = tempfile.mkstemp(prefix=".sessao-", dir=self.diretorio)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
                json.dump(dados, arquivo, ensure_ascii=False)
                arquivo.flush()
                os.fsync(arquivo.fileno())
            os.replace(caminho, self.arquivo)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(caminho)
