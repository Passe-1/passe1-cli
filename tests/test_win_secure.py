import os

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='requer Windows')


def test_criar_sem_substituir(tmp_path):
    from passe1_engine.win_secure import snapshot_pais, substituir
    destino = tmp_path / 'novo.txt'
    identidade = snapshot_pais(destino)
    substituir(destino, None, 'conteúdo', identidade=identidade)
    assert destino.read_text(encoding='utf-8') == 'conteúdo'
    with pytest.raises((OSError, ValueError)):
        substituir(destino, None, 'sobrescrito', identidade=identidade)
    assert destino.read_text(encoding='utf-8') == 'conteúdo'


def test_recusa_symlink_ancestral(tmp_path):
    from passe1_engine.win_secure import snapshot_pais, substituir
    fora = tmp_path / 'fora'
    fora.mkdir()
    dentro = tmp_path / 'projeto'
    dentro.mkdir()
    try:
        (dentro / 'alias').symlink_to(fora, target_is_directory=True)
    except OSError:
        pytest.skip('criação de symlink indisponível no runner')
    with pytest.raises((OSError, ValueError)):
        substituir(dentro / 'alias' / 'novo', None, 'x', identidade=snapshot_pais(dentro / 'alias' / 'novo'))
    assert not (fora / 'novo').exists()


def test_remover_so_se_conteudo_igual(tmp_path):
    from passe1_engine.win_secure import remover, snapshot
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('antes', encoding='utf-8')
    identidade = snapshot(alvo)['identidade']
    with pytest.raises(ValueError):
        remover(alvo, 'diferente', identidade=identidade)
    assert alvo.read_text(encoding='utf-8') == 'antes'
    remover(alvo, 'antes', identidade=identidade)
    assert not alvo.exists()


def test_remover_recusa_hardlink(tmp_path):
    from passe1_engine.win_secure import remover, snapshot
    alvo = tmp_path / 'real.txt'
    alvo.write_text('antes', encoding='utf-8')
    alias = tmp_path / 'alias.txt'
    os.link(alvo, alias)
    with pytest.raises(ValueError):
        remover(alias, 'antes', identidade=snapshot(alias)['identidade'])
    assert alvo.read_text(encoding='utf-8') == 'antes'
    assert alias.exists()




def test_criacao_falha_remove_arquivo_incompleto(tmp_path):
    from passe1_engine.win_secure import snapshot_pais, substituir
    class ConteudoInvalido:
        def encode(self, _):
            raise RuntimeError('falha simulada')
    alvo = tmp_path / 'incompleto.txt'
    with pytest.raises(RuntimeError, match='falha simulada'):
        substituir(alvo, None, ConteudoInvalido(), identidade=snapshot_pais(alvo))
    assert not alvo.exists()


def test_recusa_stream_alternativo(tmp_path):
    from passe1_engine.win_secure import snapshot_pais, substituir
    alvo = tmp_path / 'normal.txt'
    alvo.write_text('original', encoding='utf-8')
    with pytest.raises((OSError, ValueError)):
        substituir(str(alvo) + ':oculto', None, 'x', identidade=snapshot_pais(str(alvo) + ':oculto'))
    assert alvo.read_text(encoding='utf-8') == 'original'










def test_editar_preserva_conteudo_e_permissoes_basicas(tmp_path):
    from passe1_engine.win_secure import snapshot, substituir
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('antes', encoding='utf-8')
    antes = alvo.stat()
    substituir(alvo, 'antes', 'depois', identidade=snapshot(alvo)['identidade'])
    depois = alvo.stat()
    assert alvo.read_text(encoding='utf-8') == 'depois'
    assert depois.st_mode == antes.st_mode


def test_editar_recusa_versao_divergente(tmp_path):
    from passe1_engine.win_secure import snapshot, substituir
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('original', encoding='utf-8')
    with pytest.raises(ValueError):
        substituir(alvo, 'versão antiga', 'novo', identidade=snapshot(alvo)['identidade'])
    assert alvo.read_text(encoding='utf-8') == 'original'


def test_backup_criptografado_nao_contem_texto_claro(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    journal, identidade = win_secure._guardar_journal(tmp_path / 'alvo.txt', 'segredo de teste', (1, 2, 3))
    try:
        assert b'segredo de teste' not in journal.read_bytes()
        dados = win_secure._ler_journal(journal)
        assert dados['conteudo'] == 'segredo de teste'
        assert dados['identidade'] == [1, 2, 3]
    finally:
        win_secure._apagar_journal(journal, identidade)


def test_recupera_journal_apos_encerramento_durante_escrita(tmp_path, monkeypatch):
    import subprocess
    import sys

    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('conteúdo original', encoding='utf-8')
    programa = '''
import os, sys
from passe1_engine import win_secure as w
original = w._gravar_handle
def interromper(kernel, handle, texto):
    original(kernel, handle, 'parcial')
    os._exit(91)
w._gravar_handle = interromper
w.substituir(sys.argv[1], 'conteúdo original', 'novo', identidade=w.snapshot(sys.argv[1])['identidade'])
'''
    resultado = subprocess.run([sys.executable, '-c', programa, str(alvo)], check=False, timeout=20)
    assert resultado.returncode == 91
    assert alvo.read_text(encoding='utf-8') == 'parcial'
    journal = win_secure._journal_path(alvo)
    assert journal.exists()
    assert b'conte' not in journal.read_bytes()
    win_secure.recuperar(alvo)
    assert alvo.read_text(encoding='utf-8') == 'conteúdo original'
    assert not journal.exists()


def test_limpeza_do_journal_nao_apaga_arquivo_substituido(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    journal, identidade = win_secure._guardar_journal(tmp_path / 'alvo.txt', 'segredo', (1, 2, 3))
    anterior = journal.with_name('backup-original')
    journal.rename(anterior)
    journal.write_text('arquivo de terceiro', encoding='utf-8')
    with pytest.raises(ValueError, match='mudou'):
        win_secure._apagar_journal(journal, identidade)
    assert journal.read_text(encoding='utf-8') == 'arquivo de terceiro'
    assert anterior.exists()


def test_recuperacao_recusa_arquivo_substituido(tmp_path, monkeypatch):
    import subprocess
    import sys

    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('conteúdo original', encoding='utf-8')
    programa = '''
import os, sys
from passe1_engine import win_secure as w
original = w._gravar_handle
def interromper(kernel, handle, texto):
    original(kernel, handle, 'parcial')
    os._exit(91)
w._gravar_handle = interromper
w.substituir(sys.argv[1], 'conteúdo original', 'novo', identidade=w.snapshot(sys.argv[1])['identidade'])
'''
    resultado = subprocess.run([sys.executable, '-c', programa, str(alvo)], check=False, timeout=20)
    assert resultado.returncode == 91
    novo = tmp_path / 'terceiro.txt'
    novo.write_text('arquivo de terceiro', encoding='utf-8')
    os.replace(novo, alvo)
    with pytest.raises(ValueError, match='não corresponde'):
        win_secure.recuperar(alvo)
    assert alvo.read_text(encoding='utf-8') == 'arquivo de terceiro'
    assert win_secure._journal_path(alvo).exists()


def test_edicao_acima_do_limite_preserva_original(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('antes', encoding='utf-8')
    identidade = win_secure.snapshot(alvo)['identidade']
    monkeypatch.setattr(win_secure, 'LIMITE_EDICAO', 3)
    with pytest.raises(ValueError, match='limite'):
        win_secure.substituir(alvo, 'antes', 'x', identidade=identidade)
    assert alvo.read_text(encoding='utf-8') == 'antes'


def test_limpeza_journal_recusa_hardlink(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    journal, identidade = win_secure._guardar_journal(tmp_path / 'alvo.txt', 'segredo', (1, 2, 3))
    alias = journal.with_name('alias')
    os.link(journal, alias)
    with pytest.raises(ValueError, match='links'):
        win_secure._apagar_journal(journal, identidade)
    assert journal.exists() and alias.exists()


def test_falha_fsync_remove_journal_incompleto(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('original', encoding='utf-8')
    def falhar(_fd):
        raise OSError('fsync simulado')
    monkeypatch.setattr(win_secure.os, 'fsync', falhar)
    with pytest.raises(OSError, match='fsync simulado'):
        win_secure._guardar_journal(alvo, 'original', (1, 2, 3))
    assert not win_secure._journal_path(alvo).exists()
    assert alvo.read_text(encoding='utf-8') == 'original'




def test_journal_recusa_ancestral_symlink(tmp_path):
    from passe1_engine import win_secure
    real = tmp_path / 'real'
    real.mkdir()
    alias = tmp_path / 'alias'
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip('criação de symlink indisponível no runner')
    with pytest.raises((OSError, ValueError)), win_secure._ancorar_journal(alias):
        pass


def test_journal_nao_cria_diretorio_atraves_de_symlink(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    fora = tmp_path / 'fora'
    fora.mkdir()
    alias = tmp_path / 'alias'
    try:
        alias.symlink_to(fora, target_is_directory=True)
    except OSError:
        pytest.skip('criação de symlink indisponível no runner')
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(alias / 'novo'))
    with pytest.raises((OSError, ValueError)):
        win_secure._guardar_journal(tmp_path / 'alvo', 'segredo', (1, 2, 3))
    assert not (fora / 'novo').exists()


def test_journal_aberto_nao_pode_ser_renomeado(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    journal, identidade = win_secure._guardar_journal(tmp_path / 'alvo', 'segredo', (1, 2, 3))
    fd, atual = win_secure._abrir_journal(journal)
    try:
        assert atual == identidade
        with pytest.raises(OSError):
            os.rename(journal, journal.with_name('movido'))
    finally:
        os.close(fd)
    win_secure._apagar_journal(journal, identidade)


def test_edicao_recusa_journal_de_conteudo_divergente(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('antes', encoding='utf-8')
    guardar = win_secure._guardar_journal
    def corromper(caminho, _conteudo, identidade):
        return guardar(caminho, 'outro conteúdo', identidade)
    monkeypatch.setattr(win_secure, '_guardar_journal', corromper)
    with pytest.raises(ValueError, match='Journal inválido'):
        win_secure.substituir(alvo, 'antes', 'depois', identidade=win_secure.snapshot(alvo)['identidade'])
    assert alvo.read_text(encoding='utf-8') == 'antes'


def test_edicao_recusa_inode_trocado_mesmo_com_conteudo_igual(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    alvo = tmp_path / 'original.txt'
    alvo.write_text('igual', encoding='utf-8')
    identidade = win_secure.snapshot(alvo)['identidade']
    outro = tmp_path / 'outro.txt'
    outro.write_text('igual', encoding='utf-8')
    os.replace(outro, alvo)
    with pytest.raises(ValueError, match='mudou'):
        win_secure.substituir(alvo, 'igual', 'depois', identidade=identidade)
    assert alvo.read_text(encoding='utf-8') == 'igual'


def test_criacao_recusa_troca_de_diretorio_apos_preview(tmp_path):
    from passe1_engine import win_secure
    pasta = tmp_path / 'pasta'
    pasta.mkdir()
    alvo = pasta / 'novo.txt'
    identidade = win_secure.snapshot_pais(alvo)
    pasta.rename(tmp_path / 'anterior')
    pasta.mkdir()
    with pytest.raises(ValueError, match='mudou'):
        win_secure.substituir(alvo, None, 'x', identidade=identidade)
    assert not alvo.exists()
    assert not (tmp_path / 'anterior' / 'novo.txt').exists()


def test_leitura_e_identidade_compartilham_handle_e_impedem_troca(tmp_path):
    from passe1_engine import win_secure
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('conteúdo', encoding='utf-8')
    with win_secure.abrir_leitura(alvo) as (fd, identidade):
        assert os.read(fd, 100).decode('utf-8') == 'conteúdo'
        with pytest.raises(OSError):
            os.rename(alvo, tmp_path / 'movido')
    assert identidade == win_secure.snapshot(alvo)['identidade']


def test_preview_recusa_inode_substituido_com_mesmo_conteudo(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('igual', encoding='utf-8')
    with win_secure.abrir_leitura(alvo) as (fd, identidade):
        anterior = os.read(fd, 100).decode('utf-8')
    outro = tmp_path / 'outro.txt'
    outro.write_text('igual', encoding='utf-8')
    os.replace(outro, alvo)
    with pytest.raises(ValueError, match='mudou'):
        win_secure.substituir(alvo, anterior, 'alterado', identidade=identidade)
    assert alvo.read_text(encoding='utf-8') == 'igual'


def test_journal_aberto_impede_renomear_ancestrais(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    journal, identidade = win_secure._guardar_journal(tmp_path / 'alvo', 'segredo', (1, 2, 3))
    fd, atual = win_secure._abrir_journal(journal)
    try:
        assert atual == identidade
        for pasta in (journal.parent, journal.parent.parent):
            with pytest.raises(OSError):
                os.rename(pasta, pasta.with_name(pasta.name + '-movido'))
    finally:
        os.close(fd)
    win_secure._apagar_journal(journal, identidade)


def test_edicao_mantem_inode_e_descritor_de_seguranca(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes

    from passe1_engine import win_secure

    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('antes', encoding='utf-8')
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    advapi.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    def descritor():
        necessario = wintypes.DWORD()
        advapi.GetFileSecurityW(str(alvo), 7, None, 0, ctypes.byref(necessario))
        assert necessario.value > 0
        buffer = ctypes.create_string_buffer(necessario.value)
        assert advapi.GetFileSecurityW(str(alvo), 7, buffer, len(buffer), ctypes.byref(necessario))
        return buffer.raw[:necessario.value]
    antes, inode = descritor(), alvo.stat().st_ino
    identidade = win_secure.snapshot(alvo)['identidade']
    win_secure.substituir(alvo, 'antes', 'depois', identidade=identidade)
    assert alvo.read_text(encoding='utf-8') == 'depois'
    assert alvo.stat().st_ino == inode
    assert descritor() == antes


def test_edicao_preserva_stream_alternativo_existente(tmp_path, monkeypatch):
    from passe1_engine import win_secure
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('antes', encoding='utf-8')
    stream = str(alvo) + ':metadado'
    with open(stream, 'wb') as arquivo:
        arquivo.write(b'dados auxiliares')
    identidade = win_secure.snapshot(alvo)['identidade']
    win_secure.substituir(alvo, 'antes', 'depois', identidade=identidade)
    with open(stream, 'rb') as arquivo:
        assert arquivo.read() == b'dados auxiliares'
    assert alvo.read_text(encoding='utf-8') == 'depois'


def test_fluxo_integrado_windows_preview_edita_e_remove(tmp_path, monkeypatch):
    import io

    from passe1_engine import secure_stream, secure_write
    monkeypatch.setenv('PASSE1_CONFIG_DIR', str(tmp_path / 'config'))
    alvo = tmp_path / 'arquivo.txt'
    alvo.write_text('antes', encoding='utf-8')
    conteudo = io.BytesIO()
    identidade = secure_stream.transmitir(alvo, conteudo)
    assert conteudo.getvalue() == b'antes'
    secure_write.substituir(alvo, 'antes', 'depois', identidade)
    assert alvo.read_text(encoding='utf-8') == 'depois'
    conteudo = io.BytesIO()
    identidade = secure_stream.transmitir(alvo, conteudo)
    secure_write.remover(alvo, 'depois', identidade)
    assert not alvo.exists()


def test_fluxo_integrado_windows_cria_com_preview_de_pais(tmp_path):
    from passe1_engine import secure_write
    alvo = tmp_path / 'criado.txt'
    identidade = secure_write.preparar_criacao(alvo)
    secure_write.substituir(alvo, None, 'conteúdo', identidade)
    assert alvo.read_text(encoding='utf-8') == 'conteúdo'


def test_cria_arquivo_com_diretorios_ausentes_apos_preview(tmp_path):
    from passe1_engine import win_secure
    alvo = tmp_path / 'um' / 'dois' / 'arquivo.txt'
    identidade = win_secure.snapshot_pais(alvo)
    assert not (tmp_path / 'um').exists()
    resultado = win_secure.substituir(alvo, None, 'conteúdo', identidade=identidade)
    assert alvo.read_text(encoding='utf-8') == 'conteúdo'
    assert len(resultado['pais']) == len(identidade['pais']) + 2


def test_criacao_recusa_diretorio_inserido_apos_preview(tmp_path):
    from passe1_engine import win_secure
    alvo = tmp_path / 'um' / 'dois' / 'arquivo.txt'
    identidade = win_secure.snapshot_pais(alvo)
    (tmp_path / 'um').mkdir()
    with pytest.raises((OSError, ValueError)):
        win_secure.substituir(alvo, None, 'x', identidade=identidade)
    assert not alvo.exists()


def test_falha_na_criacao_reverte_diretorios_criados(tmp_path):
    from passe1_engine import win_secure
    class ConteudoInvalido:
        def encode(self, _):
            raise RuntimeError('falha simulada')
    alvo = tmp_path / 'um' / 'dois' / 'arquivo.txt'
    identidade = win_secure.snapshot_pais(alvo)
    with pytest.raises(RuntimeError, match='falha simulada'):
        win_secure.substituir(alvo, None, ConteudoInvalido(), identidade=identidade)
    assert not (tmp_path / 'um').exists()


def test_multiplas_criacoes_em_pasta_nova_usam_identidade_atualizada(tmp_path):
    from passe1_engine import win_secure
    primeiro = tmp_path / 'nova' / 'sub' / 'um.txt'
    segundo = primeiro.with_name('dois.txt')
    identidade = win_secure.snapshot_pais(primeiro)
    assert win_secure.snapshot_pais(segundo) == identidade
    criado = win_secure.substituir(primeiro, None, 'um', identidade=identidade)
    win_secure.substituir(segundo, None, 'dois', identidade={'pais': criado['pais']})
    assert primeiro.read_text(encoding='utf-8') == 'um'
    assert segundo.read_text(encoding='utf-8') == 'dois'




def test_leitura_recusa_editor_com_handle_de_escrita(tmp_path):
    import ctypes
    from ctypes import wintypes

    from passe1_engine import win_secure
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    alvo = tmp_path / 'aberto.txt'
    alvo.write_text('original', encoding='utf-8')
    editor = kernel.CreateFileW(str(alvo), 0x40000000, 7, None, 3, 0, None)
    assert editor != wintypes.HANDLE(-1).value
    try:
        with pytest.raises(OSError, match='salve e feche'), win_secure.abrir_leitura(alvo):
            pass
    finally:
        kernel.CloseHandle(editor)
    assert alvo.read_text(encoding='utf-8') == 'original'


def test_leitura_recusa_arquivo_com_mapeamento_de_escrita(tmp_path):
    import mmap

    from passe1_engine import win_secure
    alvo = tmp_path / 'mapeado.txt'
    alvo.write_text('original', encoding='utf-8')
    arquivo = alvo.open('r+b')
    mapa = mmap.mmap(arquivo.fileno(), 0, access=mmap.ACCESS_WRITE)
    arquivo.close()
    try:
        with pytest.raises(OSError, match='salve e feche'), win_secure.abrir_leitura(alvo):
            pass
    finally:
        mapa.close()
    assert alvo.read_text(encoding='utf-8') == 'original'
