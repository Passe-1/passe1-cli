"""E2E Windows do recovery interativo do move no pacote instalado."""
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='requer Windows e pywinpty')


def test_recover_move_wheel_exige_terminal_previa_e_confirmacao(tmp_path):
    from winpty import PtyProcess

    origem, destino = tmp_path / 'origem.txt', tmp_path / 'destino.txt'
    origem.write_text('origem-preview', encoding='utf-8')
    destino.write_text('destino-preview', encoding='utf-8')
    programa = '''
import os, sys
from passe1_engine import win_move, win_secure
origem, destino, raiz = sys.argv[1:]
vo = win_secure.snapshot(origem)['identidade']
vd = win_secure.snapshot(destino)['identidade']
renomear = win_move._renomear_handle
def interromper(*args):
    renomear(*args)
    os._exit(91)
win_move._renomear_handle = interromper
win_move._mover_experimental(origem, destino, 'origem-preview', 'destino-preview', 'novo-preview', vo, vd, raiz)
'''
    resultado = subprocess.run([sys.executable, '-I', '-c', programa, str(origem), str(destino), str(tmp_path)], cwd=tmp_path, capture_output=True, timeout=60, check=False)
    assert resultado.returncode == 91, resultado.stderr.decode('utf-8', errors='replace')[-1000:]
    journal = next(tmp_path.glob('move-*.journal'))
    sem_terminal = subprocess.run([sys.executable, '-I', '-m', 'passe1', 'recover-move', str(journal)], cwd=tmp_path, input='s\n', capture_output=True, text=True, encoding='utf-8', timeout=30, check=False)
    assert sem_terminal.returncode != 0
    assert not origem.exists() and destino.read_text(encoding='utf-8') == 'novo-preview'

    negado = PtyProcess.spawn([sys.executable, '-I', '-m', 'passe1', 'recover-move', str(journal)], cwd=str(tmp_path), dimensions=(30, 100))
    saida_negada = queue.Queue()

    def ler_negacao():
        try:
            while True:
                saida_negada.put(negado.read())
        except (EOFError, OSError):
            saida_negada.put(None)

    threading.Thread(target=ler_negacao, daemon=True).start()
    texto_negado, respondeu = '', False
    limite = time.monotonic() + 30
    try:
        while time.monotonic() < limite:
            try:
                parte = saida_negada.get(timeout=1)
            except queue.Empty:
                continue
            if parte is None:
                break
            texto_negado += parte
            if not respondeu and 'Restaurar o estado revisado?' in texto_negado:
                negado.write('n\r')
                respondeu = True
            if 'Recuperação cancelada' in texto_negado:
                break
    finally:
        negado.close(force=True)
    assert respondeu and 'Recuperação cancelada' in texto_negado
    assert not origem.exists() and destino.read_text(encoding='utf-8') == 'novo-preview'

    processo = PtyProcess.spawn([sys.executable, '-I', '-m', 'passe1', 'recover-move', str(journal)], cwd=str(tmp_path), dimensions=(30, 100))
    mensagens = queue.Queue()

    def ler():
        try:
            while True:
                mensagens.put(processo.read())
        except (EOFError, OSError):
            mensagens.put(None)

    threading.Thread(target=ler, daemon=True).start()
    recebido, confirmado = '', False
    limite = time.monotonic() + 30
    try:
        while time.monotonic() < limite:
            try:
                parte = mensagens.get(timeout=1)
            except queue.Empty:
                continue
            if parte is None:
                break
            recebido += parte
            if not confirmado and 'Restaurar o estado revisado?' in recebido:
                assert 'origem.txt' in recebido and 'destino.txt' in recebido
                assert 'destino-preview' in recebido and 'novo-preview' in recebido
                assert 'mesmo usuário' in recebido
                processo.write('s\r')
                confirmado = True
            if confirmado and 'Recuperação concluída' in recebido:
                break
    finally:
        processo.close(force=True)
    assert confirmado and 'Recuperação concluída' in recebido
    assert origem.read_text(encoding='utf-8') == 'origem-preview'
    assert destino.read_text(encoding='utf-8') == 'destino-preview'


def test_recover_move_destino_novo_aninhado_no_wheel(tmp_path):
    from winpty import PtyProcess

    origem = tmp_path / 'origem.txt'
    destino = tmp_path / 'nova' / 'filha' / 'destino.txt'
    origem.write_text('origem-preview', encoding='utf-8')
    programa = '''
import os, sys
from passe1_engine import win_move, win_secure
origem, destino, raiz = sys.argv[1:]
vo = win_secure.snapshot(origem)['identidade']
vd = win_secure.snapshot_pais(destino)
renomear = win_move._renomear_handle
chamadas = []
def interromper(*args):
    renomear(*args)
    chamadas.append(1)
    if len(chamadas) == 2:
        os._exit(91)
win_move._renomear_handle = interromper
win_move._mover_ausente_experimental(origem, destino, 'origem-preview', 'novo-preview', vo, vd, raiz)
'''
    resultado = subprocess.run([sys.executable, '-I', '-c', programa, str(origem), str(destino), str(tmp_path)], cwd=tmp_path, capture_output=True, timeout=60, check=False)
    assert resultado.returncode == 91, resultado.stderr.decode('utf-8', errors='replace')[-1000:]
    journal = next(tmp_path.glob('move-*.journal'))
    assert not origem.exists() and destino.read_text(encoding='utf-8') == 'novo-preview'
    adicional = destino.with_name('adicional.txt')
    adicional.write_text('preservar', encoding='utf-8')
    processo = PtyProcess.spawn([sys.executable, '-I', '-m', 'passe1', 'recover-move', str(journal)], cwd=str(tmp_path), dimensions=(30, 100))
    mensagens = queue.Queue()

    def ler():
        try:
            while True:
                mensagens.put(processo.read())
        except (EOFError, OSError):
            mensagens.put(None)

    threading.Thread(target=ler, daemon=True).start()
    recebido, confirmado = '', False
    limite = time.monotonic() + 30
    try:
        while time.monotonic() < limite:
            try:
                parte = mensagens.get(timeout=1)
            except queue.Empty:
                continue
            if parte is None:
                break
            recebido += parte
            if not confirmado and 'Restaurar o estado revisado?' in recebido:
                assert 'origem.txt' in recebido and 'destino.txt' in recebido and 'novo-preview' in recebido
                assert 'mesmo usuário' in recebido
                processo.write('s\r')
                confirmado = True
            if confirmado and 'Recuperação concluída' in recebido:
                break
    finally:
        processo.close(force=True)
    assert confirmado and 'Recuperação concluída' in recebido
    assert origem.read_text(encoding='utf-8') == 'origem-preview'
    assert not destino.exists()
    assert adicional.read_text(encoding='utf-8') == 'preservar'


def test_recover_move_sem_id_preserva_stage_para_inspecao_no_wheel(tmp_path):
    from winpty import PtyProcess

    origem = tmp_path / 'origem.txt'
    destino = tmp_path / 'nova' / 'filha' / 'destino.txt'
    origem.write_text('origem-preview', encoding='utf-8')
    programa = '''
import os, sys
from passe1_engine import win_move, win_secure
origem, destino, raiz = sys.argv[1:]
vo = win_secure.snapshot(origem)['identidade']
vd = win_secure.snapshot_pais(destino)
gravar = win_move._gravar_handle
def interromper(*args):
    gravar(*args)
    os._exit(91)
win_move._gravar_handle = interromper
win_move._mover_ausente_experimental(origem, destino, 'origem-preview', 'novo-preview', vo, vd, raiz)
'''
    resultado = subprocess.run([sys.executable, '-I', '-c', programa, str(origem), str(destino), str(tmp_path)], cwd=tmp_path, capture_output=True, timeout=60, check=False)
    assert resultado.returncode == 91, resultado.stderr.decode('utf-8', errors='replace')[-1000:]
    journal = next(tmp_path.glob('move-*.journal'))
    stage = next(destino.parent.glob('passe1-stage-*'))
    assert not Path(str(journal) + '.stage').exists()
    processo = PtyProcess.spawn([sys.executable, '-I', '-m', 'passe1', 'recover-move', str(journal)], cwd=str(tmp_path), dimensions=(30, 100))
    mensagens = queue.Queue()

    def ler():
        try:
            while True:
                mensagens.put(processo.read())
        except (EOFError, OSError):
            mensagens.put(None)

    threading.Thread(target=ler, daemon=True).start()
    recebido, confirmado = '', False
    limite = time.monotonic() + 30
    try:
        while time.monotonic() < limite:
            try:
                parte = mensagens.get(timeout=1)
            except queue.Empty:
                continue
            if parte is None:
                break
            recebido += parte
            if not confirmado and 'Restaurar o estado revisado?' in recebido:
                assert 'limpeza manual' in recebido and stage.name in recebido
                processo.write('s\r')
                confirmado = True
            if confirmado and 'Stage não apagado' in recebido:
                break
    finally:
        processo.close(force=True)
    assert confirmado and 'Stage não apagado' in recebido
    assert origem.read_text(encoding='utf-8') == 'origem-preview'
    assert not destino.exists() and stage.read_bytes() == b'novo-preview'
