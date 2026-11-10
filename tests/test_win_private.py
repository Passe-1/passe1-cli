"""Validação do staging privado de imagens no Windows."""
import os

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='requer Windows')


def test_staging_preserva_dados_e_limpa_em_falha(tmp_path):
    from passe1_engine.win_private import staging
    pasta = None
    with pytest.raises(RuntimeError, match='falha simulada'), staging([('.png', b'conteudo-teste')], parent=tmp_path) as caminhos:
        assert len(caminhos) == 1 and caminhos[0].read_bytes() == b'conteudo-teste'
        pasta = caminhos[0].parent
        raise RuntimeError('falha simulada')
    assert pasta is not None and not pasta.exists()


def test_staging_impede_troca_ou_exclusao_de_imagem(tmp_path):
    from passe1_engine.win_private import staging
    with staging([('.png', b'conteudo-teste')], parent=tmp_path) as caminhos:
        with pytest.raises(OSError):
            os.rename(caminhos[0], caminhos[0].with_name('trocada.png'))
        assert caminhos[0].read_bytes() == b'conteudo-teste'


def test_staging_recusa_extensao_invalida(tmp_path):
    from passe1_engine.win_private import staging
    with pytest.raises(ValueError), staging([('../../fora', b'x')], parent=tmp_path):
        pass


def test_dacl_do_staging_concede_acesso_apenas_ao_usuario_atual(tmp_path):
    import ctypes
    from ctypes import wintypes

    from passe1_engine.win_private import staging
    k, a = ctypes.WinDLL('kernel32', use_last_error=True), ctypes.WinDLL('advapi32', use_last_error=True)
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k.CreateFileW.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.LocalFree.argtypes = [ctypes.c_void_p]
    a.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    a.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    a.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    a.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int]
    a.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    a.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    a.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    class SidAttributes(ctypes.Structure):
        _fields_ = [('sid', ctypes.c_void_p), ('atributos', wintypes.DWORD)]
    class TokenUser(ctypes.Structure):
        _fields_ = [('user', SidAttributes)]
    class AclSize(ctypes.Structure):
        _fields_ = [('count', wintypes.DWORD), ('usado', wintypes.DWORD), ('livre', wintypes.DWORD)]
    token = wintypes.HANDLE()
    assert a.OpenProcessToken(k.GetCurrentProcess(), 8, ctypes.byref(token))
    try:
        tamanho = wintypes.DWORD()
        a.GetTokenInformation(token, 1, None, 0, ctypes.byref(tamanho))
        dados = ctypes.create_string_buffer(tamanho.value)
        assert a.GetTokenInformation(token, 1, dados, len(dados), ctypes.byref(tamanho))
        sid = ctypes.cast(dados, ctypes.POINTER(TokenUser)).contents.user.sid
        with staging([('.png', b'conteudo-teste')], parent=tmp_path) as caminhos:
            for alvo, flags in ((caminhos[0].parent, 0x02000000), (caminhos[0], 0)):
                handle = k.CreateFileW(str(alvo), 0x20000, 7, None, 3, flags, None)
                assert handle != wintypes.HANDLE(-1).value
                descritor, acl = ctypes.c_void_p(), ctypes.c_void_p()
                try:
                    assert a.GetSecurityInfo(handle, 1, 4, None, None, ctypes.byref(acl), None, ctypes.byref(descritor)) == 0
                    controle, revisao = wintypes.WORD(), wintypes.DWORD()
                    assert a.GetSecurityDescriptorControl(descritor, ctypes.byref(controle), ctypes.byref(revisao))
                    assert controle.value & 0x1000  # SE_DACL_PROTECTED
                    info = AclSize()
                    assert a.GetAclInformation(acl, ctypes.byref(info), ctypes.sizeof(info), 2)
                    assert info.count == 1
                    ace = ctypes.c_void_p()
                    assert a.GetAce(acl, 0, ctypes.byref(ace))
                    assert ctypes.string_at(ace, 1) == b'\x00'  # ACCESS_ALLOWED_ACE
                    assert a.EqualSid(sid, ace.value + 8)
                finally:
                    if descritor:
                        k.LocalFree(descritor)
                    k.CloseHandle(handle)
    finally:
        k.CloseHandle(token)


def test_staging_impede_renomear_diretorio_enquanto_imagem_aberta(tmp_path):
    from passe1_engine.win_private import staging
    with staging([('.png', b'conteudo-teste')], parent=tmp_path) as caminhos:
        pasta = caminhos[0].parent
        with pytest.raises(OSError):
            os.rename(pasta, pasta.with_name('movido'))
        assert caminhos[0].read_bytes() == b'conteudo-teste'


def test_staging_recusa_modificacao_entre_handles(tmp_path, monkeypatch):
    import ctypes

    from passe1_engine.win_private import staging

    carregar = ctypes.WinDLL
    alterado = []

    class Funcao:
        def __init__(self, original, depois):
            self.original = original
            self.depois = depois

        def __call__(self, *args):
            resultado = self.original(*args)
            self.depois()
            return resultado

        @property
        def argtypes(self):
            return self.original.argtypes

        @argtypes.setter
        def argtypes(self, valor):
            self.original.argtypes = valor

    class Kernel:
        def __init__(self, original):
            self.original = original
            self.gravado = False
            self.WriteFile = Funcao(original.WriteFile, self.marcar)
            self.CloseHandle = Funcao(original.CloseHandle, self.atacar)

        def __getattr__(self, nome):
            return getattr(self.original, nome)

        def marcar(self):
            self.gravado = True

        def atacar(self):
            if self.gravado and not alterado:
                arquivo = next(tmp_path.glob('passe1-*/*.png'))
                arquivo.write_bytes(b'conteudo-alterado')
                alterado.append(True)

    def carregar_interceptado(nome, *args, **kwargs):
        biblioteca = carregar(nome, *args, **kwargs)
        return Kernel(biblioteca) if nome == 'kernel32' else biblioteca

    monkeypatch.setattr(ctypes, 'WinDLL', carregar_interceptado)
    with pytest.raises(ValueError, match='Conteúdo da imagem mudou'), staging([('.png', b'conteudo-teste')], parent=tmp_path):
        pytest.fail('A imagem modificada não pode ser entregue.')
    assert alterado
    assert not list(tmp_path.glob('passe1-*'))


def test_mapeamento_gravavel_na_janela_nao_engana_digest_do_consumidor(tmp_path, monkeypatch):
    import ctypes
    import hashlib
    import mmap
    import shutil

    from passe1_engine.win_private import staging

    carregar = ctypes.WinDLL
    mapas = []
    dados = b'conteudo-teste'
    entregue = False

    class Funcao:
        def __init__(self, original, depois):
            self.original, self.depois = original, depois

        def __call__(self, *args):
            resultado = self.original(*args)
            self.depois()
            return resultado

        @property
        def argtypes(self):
            return self.original.argtypes

        @argtypes.setter
        def argtypes(self, valor):
            self.original.argtypes = valor

    class Kernel:
        def __init__(self, original):
            self.original, self.gravado = original, False
            self.WriteFile = Funcao(original.WriteFile, self.marcar)
            self.CloseHandle = Funcao(original.CloseHandle, self.mapear)

        def __getattr__(self, nome):
            return getattr(self.original, nome)

        def marcar(self):
            self.gravado = True

        def mapear(self):
            if self.gravado and not mapas:
                arquivo = next(tmp_path.glob('passe1-*/*.png'))
                with arquivo.open('r+b') as aberto:
                    mapas.append(mmap.mmap(aberto.fileno(), 0, access=mmap.ACCESS_WRITE))

    def carregar_interceptado(nome, *args, **kwargs):
        biblioteca = carregar(nome, *args, **kwargs)
        return Kernel(biblioteca) if nome == 'kernel32' else biblioteca

    monkeypatch.setattr(ctypes, 'WinDLL', carregar_interceptado)
    try:
        try:
            with staging([('.png', dados)], parent=tmp_path) as caminhos:
                entregue = True
                mapas[0][0] = ord('X')
                consumido = caminhos[0].read_bytes()
                assert hashlib.sha256(consumido).digest() != hashlib.sha256(dados).digest()
                mapas[0].close()
        except OSError:
            # Um mapeamento ativo pode impedir a abertura segura ou a limpeza.
            # Nesse caso não houve consumo; a falha deve preceder o yield.
            assert not entregue
    finally:
        for mapa in mapas:
            if not mapa.closed:
                mapa.close()
        for pasta in tmp_path.glob('passe1-*'):
            shutil.rmtree(pasta)
    assert mapas
