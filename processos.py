"""Captura limitada e encerramento de processos em modo POSIX."""
from __future__ import annotations

import os
import selectors
import signal
import subprocess
import threading
import time
from pathlib import Path


def _terminou_sem_recolher(pid: int) -> bool:
    if hasattr(os, "waitid"):
        return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    # Python 3.10 no macOS não expõe waitid; o processo zumbi mantém o PID reservado.
    resultado = subprocess.run(
        ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=5, check=False,
    )
    if resultado.returncode or not resultado.stdout.strip():
        raise RuntimeError("Não foi possível verificar o processo do comando.")
    return resultado.stdout.strip().startswith("Z")


def _encerrar_grupo(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        # No macOS, sinalizar grupo composto apenas por zumbis retorna EPERM.
        resultado = subprocess.run(
            ["/bin/ps", "-axo", "pgid=,stat="], capture_output=True, text=True, timeout=5, check=False,
        )
        if resultado.returncode:
            raise RuntimeError("Não foi possível verificar o grupo do comando.") from None
        for linha in resultado.stdout.splitlines():
            partes = linha.split()
            if len(partes) >= 2 and partes[0] == str(pid) and not partes[1].startswith("Z"):
                raise RuntimeError("Não foi possível encerrar todos os processos do comando.") from None


def _executar_windows(comando, raiz, timeout, limite, shell):
    """Captura limitada em plataforma sem grupos POSIX; encerra o processo principal."""
    proc = subprocess.Popen(
        comando, shell=shell, cwd=raiz, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, bufsize=0,
    )
    dados = bytearray()
    estado = {"truncado": False}
    trava = threading.Lock()
    parar = threading.Event()

    def drenar():
        try:
            while not parar.is_set():
                trecho = proc.stdout.read(65536)
                if not trecho:
                    break
                with trava:
                    espaco = max(0, limite - len(dados))
                    dados.extend(trecho[:espaco])
                    estado["truncado"] |= len(trecho) > espaco
        except OSError:
            pass
        finally:
            proc.stdout.close()

    leitor = threading.Thread(target=drenar, daemon=True)
    leitor.start()
    try:
        codigo, expirou = proc.wait(timeout=timeout), False
    except BaseException as exc:
        proc.kill()
        proc.wait()
        if not isinstance(exc, subprocess.TimeoutExpired):
            parar.set()
            raise
        codigo, expirou = None, True
    leitor.join(timeout=0.3)
    parar.set()
    with trava:
        texto = dados.decode("utf-8", errors="replace")
        if estado["truncado"]:
            texto += "\n[saída truncada]"
    return codigo, texto, expirou


def executar(comando, raiz: Path, timeout: float = 120, limite: int = 30000, *, shell: bool = False):
    """Retorna (código, saída, expirou), sem gravar saída ilimitada em disco."""
    projeto = Path(raiz).resolve(strict=True)
    if not projeto.is_dir():
        raise ValueError("Diretório do projeto inválido.")
    if os.name == "nt":
        return _executar_windows(comando, projeto, timeout, limite, shell)
    if os.name != "posix":
        raise RuntimeError("Execução com captura segura indisponível nesta plataforma.")
    proc = subprocess.Popen(
        comando, shell=shell, cwd=projeto, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=True,
    )
    dados = bytearray()
    truncado = False
    limite_tempo = time.monotonic() + timeout
    grupo_encerrado = False
    try:
        with selectors.DefaultSelector() as seletor:
            seletor.register(proc.stdout, selectors.EVENT_READ)
            while True:
                terminou = _terminou_sem_recolher(proc.pid)
                if terminou and not grupo_encerrado:
                    # O líder ainda não foi recolhido; seu PID não pode ser reutilizado.
                    _encerrar_grupo(proc.pid)
                    grupo_encerrado = True
                restante = limite_tempo - time.monotonic()
                if restante <= 0:
                    if not terminou:
                        raise subprocess.TimeoutExpired(comando, timeout)
                    break
                eventos = seletor.select(0 if terminou else min(restante, 0.1))
                if not eventos:
                    if terminou:
                        break
                    continue
                trecho = os.read(proc.stdout.fileno(), 65536)
                if not trecho:
                    seletor.unregister(proc.stdout)
                    if terminou:
                        break
                    time.sleep(min(restante, 0.05))
                    continue
                espaco = max(0, limite - len(dados))
                dados.extend(trecho[:espaco])
                truncado |= len(trecho) > espaco
        codigo, expirou = proc.wait(), False
    except BaseException as exc:
        _encerrar_grupo(proc.pid)
        proc.wait()
        if not isinstance(exc, subprocess.TimeoutExpired):
            raise
        codigo, expirou = None, True
    finally:
        proc.stdout.close()
    texto = dados.decode("utf-8", errors="replace")
    if truncado:
        texto += "\n[saída truncada]"
    return codigo, texto, expirou
