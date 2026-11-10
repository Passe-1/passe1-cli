"""Hipótese refutada: disposition FALSE cancela FILE_DELETE_ON_CLOSE.

Executar manualmente no Windows com pytest: o teste deve falhar em NTFS,
pois o arquivo continua sendo excluído quando o último handle fecha.
"""
import ctypes
import msvcrt
from ctypes import wintypes

from passe1_engine import win_move, win_secure


def test_hipotese_cancelar_delete_on_close(tmp_path):
    destino = tmp_path / 'destino-novo.txt'
    pais = win_secure.snapshot_pais(destino)['pais']
    with win_move._preparar_arquivo_temporario(destino, pais) as (fd, _pai, nome, _identidade):
        win_move._gravar_handle(fd, b'conteudo')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        excluir = wintypes.BOOL(False)
        assert kernel.SetFileInformationByHandle(msvcrt.get_osfhandle(fd), 4, ctypes.byref(excluir), ctypes.sizeof(excluir))
    assert (tmp_path / nome).read_bytes() == b'conteudo'
