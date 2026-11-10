import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from passe1_engine import secure_move, secure_stream

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='transação POSIX')



def recuperar_confirmado(journal):
    revisao = secure_move.revisar_local(journal)
    return secure_move.recuperar(journal, revisao['revisao'], confirmado=True)

def preparar(tmp_path):
    origem = tmp_path / 'origem'
    destino = tmp_path / 'destino'
    journal = tmp_path / 'journals'
    journal.mkdir(mode=0o700)
    origem.write_text('origem aprovada\n')
    destino.write_text('destino aprovado\n')
    versao_origem = secure_stream.transmitir(origem, io.BytesIO())
    versao_destino = secure_stream.transmitir(destino, io.BytesIO())
    return origem, destino, journal, versao_origem, versao_destino


def executar(dados):
    origem, destino, journal, vo, vd = dados
    return secure_move.mover(origem, destino, 'origem aprovada\n', 'destino aprovado\n', 'resultado\n', vo, vd, journal)


def test_move_preserva_quarentena_e_permite_recuperacao(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, journal, _, _ = dados
    resultado = executar(dados)
    assert not origem.exists()
    assert destino.read_text() == 'resultado\n'
    assert resultado.parent == journal
    recuperar_confirmado(resultado)
    assert origem.read_text() == 'origem aprovada\n'
    assert destino.read_text() == 'destino aprovado\n'


def test_origem_trocada_apos_aprovacao_nao_altera_destino(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, *_ = dados
    origem.rename(tmp_path / 'original')
    origem.write_text('origem aprovada\n')
    with pytest.raises(ValueError, match='mudou'):
        executar(dados)
    assert destino.read_text() == 'destino aprovado\n'
    assert origem.read_text() == 'origem aprovada\n'


def test_falha_apos_guardar_origem_restabelece_arquivos(tmp_path, monkeypatch):
    dados = preparar(tmp_path)
    origem, destino, journal, *_ = dados
    original = secure_move._exclusivo
    contador = []
    def falhar(*args):
        contador.append(True)
        if len(contador) == 2:
            raise OSError('falha simulada')
        return original(*args)
    monkeypatch.setattr(secure_move, '_exclusivo', falhar)
    with pytest.raises(RuntimeError, match='journal preservado'):
        executar(dados)
    assert not origem.exists()
    assert destino.read_text() == 'destino aprovado\n'
    (entrada,) = journal.iterdir()
    recuperar_confirmado(entrada)
    assert origem.read_text() == 'origem aprovada\n'
    assert destino.read_text() == 'destino aprovado\n'


def test_recupera_apos_interrupcao_entre_renames(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, journal, vo, vd = dados
    codigo = '''
import json, os, sys
from passe1_engine import secure_move
orig = secure_move._manifesto
def interromper(fd, dados):
    if dados.get('fase') == 'destino_guardado':
        os._exit(91)
    orig(fd, dados)
secure_move._manifesto = interromper
secure_move.mover(sys.argv[1], sys.argv[2], 'origem aprovada\\n', 'destino aprovado\\n', 'resultado\\n', json.loads(sys.argv[3]), json.loads(sys.argv[4]), sys.argv[5])
'''
    resultado = subprocess.run([sys.executable, '-c', codigo, str(origem), str(destino), json.dumps(vo), json.dumps(vd), str(journal)], cwd=Path(__file__).resolve().parents[1], check=False)
    assert resultado.returncode == 91
    (entrada,) = journal.iterdir()
    recuperar_confirmado(entrada)
    assert origem.read_text() == 'origem aprovada\n'
    assert destino.read_text() == 'destino aprovado\n'


def test_recuperacao_nao_sobrescreve_destino_alterado(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, _, *_ = dados
    journal = executar(dados)
    destino.write_text('alteração posterior')
    with pytest.raises(ValueError, match='Destino foi alterado'):
        recuperar_confirmado(journal)
    assert destino.read_text() == 'alteração posterior'
    assert not origem.exists()


def test_troca_de_origem_imediatamente_antes_de_rename_preserva_destino(tmp_path, monkeypatch):
    dados = preparar(tmp_path)
    origem, destino, *_ = dados
    original = secure_move._exclusivo
    chamadas = []
    def trocar(*args):
        chamadas.append(True)
        if len(chamadas) == 1:
            origem.rename(tmp_path / 'terceiro-original')
            origem.write_text('arquivo de terceiro')
        return original(*args)
    monkeypatch.setattr(secure_move, '_exclusivo', trocar)
    with pytest.raises(RuntimeError, match='journal preservado'):
        executar(dados)
    assert not origem.exists()
    (quarentena,) = origem.parent.glob('.passe1-move-*-origem')
    assert (quarentena / 'origem').read_text() == 'arquivo de terceiro'
    assert (tmp_path / 'terceiro-original').read_text() == 'origem aprovada\n'
    assert destino.read_text() == 'destino aprovado\n'


def test_troca_de_destino_imediatamente_antes_de_rename_preserva_ambos(tmp_path, monkeypatch):
    dados = preparar(tmp_path)
    origem, destino, *_ = dados
    original = secure_move._exclusivo
    chamadas = []
    def trocar(*args):
        chamadas.append(True)
        if len(chamadas) == 2:
            destino.rename(tmp_path / 'destino-original')
            destino.write_text('arquivo de terceiro')
        return original(*args)
    monkeypatch.setattr(secure_move, '_exclusivo', trocar)
    with pytest.raises(RuntimeError, match='journal preservado'):
        executar(dados)
    assert not origem.exists()
    assert not destino.exists()
    (origem_guardada,) = origem.parent.glob('.passe1-move-*-origem')
    (destino_guardado,) = destino.parent.glob('.passe1-move-*-destino')
    assert (origem_guardada / 'origem').read_text() == 'origem aprovada\n'
    assert (destino_guardado / 'destino').read_text() == 'arquivo de terceiro'
    assert (tmp_path / 'destino-original').read_text() == 'destino aprovado\n'


def test_crash_antes_de_preparar_journal_nao_altera_arquivos(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, journal, vo, vd = dados
    codigo = '''
import json, os, sys
from passe1_engine import secure_move
orig = secure_move._manifesto
def interromper(fd, dados):
    if dados.get('fase') == 'preparado':
        os._exit(91)
    orig(fd, dados)
secure_move._manifesto = interromper
secure_move.mover(sys.argv[1], sys.argv[2], 'origem aprovada\\n', 'destino aprovado\\n', 'resultado\\n', json.loads(sys.argv[3]), json.loads(sys.argv[4]), sys.argv[5])
'''
    resultado = subprocess.run([sys.executable, '-c', codigo, str(origem), str(destino), json.dumps(vo), json.dumps(vd), str(journal)], cwd=Path(__file__).resolve().parents[1], check=False)
    assert resultado.returncode == 91
    (entrada,) = journal.iterdir()
    recuperar_confirmado(entrada)
    assert origem.read_text() == 'origem aprovada\n'
    assert destino.read_text() == 'destino aprovado\n'


def test_recupera_apos_crash_depois_da_publicacao(tmp_path):
    origem, destino, journal, vo, vd = preparar(tmp_path)
    codigo = '''
import json, os, sys
from passe1_engine import secure_move
orig = secure_move._manifesto
def interromper(fd, dados):
    if dados.get('fase') == 'aplicado':
        os._exit(91)
    orig(fd, dados)
secure_move._manifesto = interromper
secure_move.mover(sys.argv[1], sys.argv[2], 'origem aprovada\\n', 'destino aprovado\\n', 'resultado\\n', json.loads(sys.argv[3]), json.loads(sys.argv[4]), sys.argv[5])
'''
    resultado = subprocess.run([sys.executable, '-c', codigo, str(origem), str(destino), json.dumps(vo), json.dumps(vd), str(journal)], cwd=Path(__file__).resolve().parents[1], check=False)
    assert resultado.returncode == 91
    assert destino.read_text() == 'resultado\n'
    (entrada,) = journal.iterdir()
    recuperar_confirmado(entrada)
    assert origem.read_text() == 'origem aprovada\n'
    assert destino.read_text() == 'destino aprovado\n'


def test_recusa_hardlink_e_preserva_destino(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, *_ = dados
    os.link(origem, tmp_path / 'alias')
    with pytest.raises(ValueError, match='links'):
        executar(dados)
    assert destino.read_text() == 'destino aprovado\n'


def test_recusa_symlink_ancestral_antes_de_qualquer_mutacao(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, journal, vo, vd = dados
    pasta = tmp_path / 'pasta'
    pasta.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(pasta, target_is_directory=True)
    with pytest.raises(OSError):
        secure_move.mover(alias / 'origem', destino, 'origem aprovada\n', 'destino aprovado\n', 'resultado\n', vo, vd, journal)
    assert origem.read_text() == 'origem aprovada\n'
    assert destino.read_text() == 'destino aprovado\n'


def test_move_entre_diretorios_preserva_permissoes_e_journal_privado(tmp_path):
    import stat
    pasta_origem = tmp_path / 'a'
    pasta_destino = tmp_path / 'b'
    pasta_origem.mkdir()
    pasta_destino.mkdir()
    origem = pasta_origem / 'origem'
    destino = pasta_destino / 'destino'
    journal_root = tmp_path / 'journals'
    journal_root.mkdir(mode=0o700)
    origem.write_text('origem aprovada\n')
    destino.write_text('destino aprovado\n')
    destino.chmod(0o640)
    vo = secure_stream.transmitir(origem, io.BytesIO())
    vd = secure_stream.transmitir(destino, io.BytesIO())
    journal = secure_move.mover(origem, destino, 'origem aprovada\n', 'destino aprovado\n', 'resultado\n', vo, vd, journal_root)
    assert not origem.exists()
    assert destino.read_text() == 'resultado\n'
    assert stat.S_IMODE(destino.stat().st_mode) == 0o640
    assert stat.S_IMODE(journal.stat().st_mode) == 0o700
    assert stat.S_IMODE((journal / 'manifesto.json').stat().st_mode) == 0o600
    recuperar_confirmado(journal)
    assert origem.read_text() == 'origem aprovada\n'
    assert destino.read_text() == 'destino aprovado\n'
    assert stat.S_IMODE(destino.stat().st_mode) == 0o640


def test_recusa_raiz_de_journal_acessivel_por_outros(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, journal, *_ = dados
    journal.chmod(0o755)
    with pytest.raises(ValueError, match='não é privado'):
        executar(dados)
    assert origem.read_text() == 'origem aprovada\n'
    assert destino.read_text() == 'destino aprovado\n'


def test_troca_do_staging_nao_apaga_originais(tmp_path, monkeypatch):
    dados = preparar(tmp_path)
    origem, destino, journal, *_ = dados
    original = secure_move._exclusivo
    chamadas = []
    def trocar(*args):
        chamadas.append(True)
        if len(chamadas) == 3:
            quarentena, = destino.parent.glob('.passe1-move-*-destino')
            staged = quarentena / 'novo'
            staged.rename(tmp_path / 'staging-original')
            staged.write_text('arquivo de terceiro')
        return original(*args)
    monkeypatch.setattr(secure_move, '_exclusivo', trocar)
    with pytest.raises(RuntimeError, match='journal preservado'):
        executar(dados)
    assert destino.read_text() == 'arquivo de terceiro'
    assert (tmp_path / 'staging-original').read_text() == 'resultado\n'
    (entrada,) = journal.iterdir()
    dados_journal = json.loads((entrada / 'manifesto.json').read_text())
    assert (origem.parent / dados_journal['hold_origem'] / 'origem').read_text() == 'origem aprovada\n'
    assert (destino.parent / dados_journal['hold_destino'] / 'destino').read_text() == 'destino aprovado\n'


@pytest.mark.skipif(sys.platform != 'darwin', reason='ACL estendida específica do macOS')
def test_recusa_journal_com_acl_estendida(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, journal, *_ = dados
    subprocess.run(['chmod', '+a', 'everyone allow readattr', str(journal)], check=True)
    with pytest.raises(ValueError, match='ACL estendida'):
        executar(dados)
    assert origem.read_text() == 'origem aprovada\n'
    assert destino.read_text() == 'destino aprovado\n'


def test_troca_do_backup_antes_da_leitura_e_detectada(tmp_path, monkeypatch):
    dados = preparar(tmp_path)
    _, destino, journal_root, *_ = dados
    leitura_original = secure_move._ler_guardado
    trocado = []
    def trocar(pai, nome):
        if nome == 'origem' and not trocado:
            os.rename(nome, 'origem-real', src_dir_fd=pai, dst_dir_fd=pai)
            fd = os.open(nome, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=pai)
            try:
                os.write(fd, b'origem aprovada\n')
            finally:
                os.close(fd)
            trocado.append(True)
        return leitura_original(pai, nome)
    monkeypatch.setattr(secure_move, '_ler_guardado', trocar)
    with pytest.raises(RuntimeError, match='journal preservado'):
        executar(dados)
    assert trocado
    assert destino.read_text() == 'destino aprovado\n'
    quarentena, = tmp_path.glob('.passe1-move-*-origem')
    assert (quarentena / 'origem-real').read_text() == 'origem aprovada\n'
    assert len(list(journal_root.iterdir())) == 1


def test_quarentena_privada_e_inacessivel_as_ferramentas_publicas(tmp_path):
    import agente
    import arquivos_seguros
    dados = preparar(tmp_path)
    origem, destino, journal_root, *_ = dados
    journal = executar(dados)
    manifesto = json.loads((journal / 'manifesto.json').read_text())
    backup = origem.parent / manifesto['hold_origem'] / 'origem'
    import stat
    assert stat.S_IMODE(backup.parent.stat().st_mode) == 0o700
    with pytest.raises(ValueError, match='(?i)quarentena'):
        arquivos_seguros.ler(Path('/'), backup)
    with pytest.raises(ValueError, match='(?i)quarentena'):
        secure_stream.transmitir(backup, io.BytesIO())
    resultado = agente.executar_ferramenta(tmp_path, 'ler_arquivo', {'caminho': str(backup.relative_to(tmp_path))}, lambda _: 's')
    assert 'origem aprovada' not in resultado
    assert 'quarentena' in resultado.lower()
    from passe1_engine import secure_search
    import shutil
    ripgrep = shutil.which('rg')
    if ripgrep:
        assert secure_search.buscar_candidatos('origem aprovada', [str(backup)], ripgrep) == []
    assert journal.parent == journal_root
    assert destino.read_text() == 'resultado\n'


def test_recuperacao_recusa_quarentena_substituida_por_symlink(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, _, *_ = dados
    journal = executar(dados)
    manifesto = json.loads((journal / 'manifesto.json').read_text())
    quarentena = origem.parent / manifesto['hold_origem']
    quarentena.rename(tmp_path / 'quarentena-original')
    externo = tmp_path / 'terceiros'
    externo.mkdir()
    (externo / 'origem').write_text('terceiro')
    quarentena.symlink_to(externo, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        recuperar_confirmado(journal)
    assert (externo / 'origem').read_text() == 'terceiro'
    assert destino.read_text() == 'resultado\n'
    assert (tmp_path / 'quarentena-original' / 'origem').read_text() == 'origem aprovada\n'


def test_recuperacao_recusa_journal_forgado_com_nomes_arbitrarios(tmp_path):
    dados = preparar(tmp_path)
    journal = executar(dados)
    manifesto = journal / 'manifesto.json'
    conteudo = json.loads(manifesto.read_text())
    conteudo['hold_origem'] = '../terceiro'
    manifesto.write_text(json.dumps(conteudo))
    with pytest.raises(ValueError, match='Journal inválido'):
        recuperar_confirmado(journal)


@pytest.mark.skipif(sys.platform != 'darwin', reason='ACL herdada específica do macOS')
def test_recusa_acl_herdada_na_quarentena_sem_alterar_arquivos(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, _, *_ = dados
    subprocess.run(['chmod', '+a', 'everyone allow readattr,file_inherit,directory_inherit', str(tmp_path)], check=True)
    with pytest.raises(RuntimeError, match='journal preservado'):
        executar(dados)
    assert origem.read_text() == 'origem aprovada\n'
    assert destino.read_text() == 'destino aprovado\n'


def test_revisao_mostra_caminhos_sem_conteudo_e_exige_confirmacao(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, _, *_ = dados
    journal = executar(dados)
    plano = secure_move.revisar_local(journal)
    assert plano['origem'] == str(origem)
    assert plano['destino'] == str(destino)
    assert [item['acao'] for item in plano['acoes']] == ['guardar_resultado', 'restaurar_destino', 'restaurar_origem']
    metadados = secure_move.revisar(journal)
    assert 'origem aprovada' not in json.dumps(metadados)
    assert 'destino aprovado' not in json.dumps(metadados)
    assert 'revisao' not in metadados
    assert 'origem aprovada' in plano['previa']
    assert 'destino aprovado' in plano['previa']
    assert 'mesmo usuário' in plano['aviso']
    with pytest.raises(ValueError, match='confirme explicitamente'):
        secure_move.recuperar(journal, plano['revisao'])
    assert not origem.exists() and destino.read_text() == 'resultado\n'
    secure_move.recuperar(journal, plano['revisao'], confirmado=True)
    assert origem.read_text() == 'origem aprovada\n'


def test_recuperacao_recusa_journal_modificado_depois_da_revisao(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, _, *_ = dados
    journal = executar(dados)
    plano = secure_move.revisar_local(journal)
    manifesto = journal / 'manifesto.json'
    dados_manifesto = json.loads(manifesto.read_text())
    dados_manifesto['fase'] = 'destino_guardado'
    manifesto.write_text(json.dumps(dados_manifesto))
    with pytest.raises(ValueError, match='Estado mudou'):
        secure_move.recuperar(journal, plano['revisao'], confirmado=True)
    assert not origem.exists() and destino.read_text() == 'resultado\n'


def test_recuperacao_recusa_quarentena_alterada_apos_revisao(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, _, *_ = dados
    journal = executar(dados)
    plano = secure_move.revisar_local(journal)
    manifesto = json.loads((journal / 'manifesto.json').read_text())
    guardado = origem.parent / manifesto['hold_origem'] / 'origem'
    guardado.write_text('mudança de terceiro')
    with pytest.raises(ValueError, match='substituíd'):
        secure_move.recuperar(journal, plano['revisao'], confirmado=True)
    assert guardado.read_text() == 'mudança de terceiro'
    assert not origem.exists() and destino.read_text() == 'resultado\n'


def test_recuperacao_recusa_backup_trocado_com_mesmo_conteudo(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, _, *_ = dados
    journal = executar(dados)
    plano = secure_move.revisar_local(journal)
    manifesto = json.loads((journal / 'manifesto.json').read_text())
    guardado = origem.parent / manifesto['hold_origem'] / 'origem'
    guardado.rename(guardado.with_name('outro'))
    guardado.write_text('origem aprovada\n')
    with pytest.raises(ValueError, match='substituíd'):
        secure_move.recuperar(journal, plano['revisao'], confirmado=True)
    assert guardado.read_text() == 'origem aprovada\n'
    assert not origem.exists() and destino.read_text() == 'resultado\n'


def test_protocolo_nao_restaura_sem_confirmacao_explicita(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, _, *_ = dados
    journal = executar(dados)
    plano = secure_move.revisar_local(journal)
    entrada = {'operacao': 'recuperar', 'journal': str(journal), 'revisao': plano['revisao']}
    processo = subprocess.run(
        [sys.executable, '-m', 'passe1_engine.secure_move'],
        input=json.dumps(entrada), text=True, capture_output=True,
        cwd=Path(__file__).resolve().parents[1], check=False,
    )
    assert processo.returncode != 0
    assert 'somente no comando local' in processo.stderr
    assert 'origem aprovada' not in processo.stdout + processo.stderr
    assert not origem.exists() and destino.read_text() == 'resultado\n'


def test_previa_local_sanitiza_conteudo_e_caminhos_de_controle(tmp_path):
    origem = tmp_path / 'origem\x1b[31m\nextra'
    destino = tmp_path / 'destino'
    journal_root = tmp_path / 'journals'
    journal_root.mkdir(mode=0o700)
    origem.write_text('ORIGEM\x1b[2J\u202eINVISIVEL\n')
    destino.write_text('DESTINO\x07\n')
    vo = secure_stream.transmitir(origem, io.BytesIO())
    vd = secure_stream.transmitir(destino, io.BytesIO())
    journal = secure_move.mover(origem, destino, origem.read_text(), destino.read_text(), 'resultado\n', vo, vd, journal_root)
    plano = secure_move.revisar_local(journal)
    assert '\x1b' not in plano['previa'] and '\x07' not in plano['previa'] and '\u202e' not in plano['previa']
    assert r'\x1b' in plano['previa'] and r'\x07' in plano['previa'] and r'\u202e' in plano['previa']
    assert r'\nextra' in plano['previa']
    assert '\x1b' not in plano['origem'] and '\n' not in plano['origem']
    assert r'\x1b' in plano['origem'] and r'\nextra' in plano['origem']
    metadados = secure_move.revisar(journal)
    assert 'previa' not in metadados
    assert 'ORIGEM' not in json.dumps(metadados) and 'DESTINO' not in json.dumps(metadados)


def test_recuperacao_detecta_objeto_trocado_no_rename_do_resultado(tmp_path, monkeypatch):
    origem, destino, _, *_ = (dados := preparar(tmp_path))
    journal = executar(dados)
    plano = secure_move.revisar_local(journal)
    original = secure_move._exclusivo
    chamadas = []
    def trocar(*args):
        chamadas.append(True)
        if len(chamadas) == 1:
            destino.rename(tmp_path / 'resultado-original')
            destino.write_text('terceiro')
        return original(*args)
    monkeypatch.setattr(secure_move, '_exclusivo', trocar)
    with pytest.raises(RuntimeError, match='Estado incerto.*journal|Estado incerto.*Journal'):
        secure_move.recuperar(journal, plano['revisao'], confirmado=True)
    assert not origem.exists() and not destino.exists()
    assert (tmp_path / 'resultado-original').read_text() == 'resultado\n'
    manifesto = json.loads((journal / 'manifesto.json').read_text())
    assert (destino.parent / manifesto['hold_destino'] / 'novo').read_text() == 'terceiro'
    assert (destino.parent / manifesto['hold_destino'] / 'destino').read_text() == 'destino aprovado\n'
    assert (origem.parent / manifesto['hold_origem'] / 'origem').read_text() == 'origem aprovada\n'


def test_recuperacao_detecta_backup_trocado_no_rename(tmp_path, monkeypatch):
    origem, destino, _, *_ = (dados := preparar(tmp_path))
    journal = executar(dados)
    plano = secure_move.revisar_local(journal)
    manifesto = json.loads((journal / 'manifesto.json').read_text())
    backup = destino.parent / manifesto['hold_destino'] / 'destino'
    original = secure_move._exclusivo
    chamadas = []
    def trocar(*args):
        chamadas.append(True)
        if len(chamadas) == 2:
            backup.rename(backup.with_name('destino-real'))
            backup.write_text('terceiro')
        return original(*args)
    monkeypatch.setattr(secure_move, '_exclusivo', trocar)
    with pytest.raises(RuntimeError, match='Estado incerto'):
        secure_move.recuperar(journal, plano['revisao'], confirmado=True)
    assert not destino.exists()
    (incidente,) = backup.parent.glob('incidente-*')
    assert incidente.read_text() == 'terceiro'
    assert backup.with_name('destino-real').read_text() == 'destino aprovado\n'
    assert (origem.parent / manifesto['hold_origem'] / 'origem').read_text() == 'origem aprovada\n'


def test_recuperacao_detecta_origem_trocada_no_rename(tmp_path, monkeypatch):
    origem, destino, _, *_ = (dados := preparar(tmp_path))
    journal = executar(dados)
    plano = secure_move.revisar_local(journal)
    manifesto = json.loads((journal / 'manifesto.json').read_text())
    backup = origem.parent / manifesto['hold_origem'] / 'origem'
    original = secure_move._exclusivo
    chamadas = []
    def trocar(*args):
        chamadas.append(True)
        if len(chamadas) == 3:
            backup.rename(backup.with_name('origem-real'))
            backup.write_text('terceiro')
        return original(*args)
    monkeypatch.setattr(secure_move, '_exclusivo', trocar)
    with pytest.raises(RuntimeError, match='Estado incerto'):
        secure_move.recuperar(journal, plano['revisao'], confirmado=True)
    assert not origem.exists()
    (incidente,) = backup.parent.glob('incidente-*')
    assert incidente.read_text() == 'terceiro'
    assert backup.with_name('origem-real').read_text() == 'origem aprovada\n'
    assert destino.read_text() == 'destino aprovado\n'


def test_token_nao_pode_ser_derivado_da_revisao_sem_previa_local(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, _, *_ = dados
    journal = executar(dados)
    metadados = secure_move.revisar(journal)
    codigo_falso = secure_move._codigo_revisao(metadados)
    with pytest.raises(ValueError, match='Revisão local ausente'):
        secure_move.recuperar(journal, codigo_falso, confirmado=True)
    assert not origem.exists() and destino.read_text() == 'resultado\n'


def test_previa_inteira_e_obrigatoria_antes_da_confirmacao(tmp_path, monkeypatch):
    origem = tmp_path / 'origem'
    destino = tmp_path / 'destino'
    journal_root = tmp_path / 'journals'
    journal_root.mkdir(mode=0o700)
    original = 'x' * (1024 * 1024 + 1)
    origem.write_text(original)
    destino.write_text('antes')
    vo = secure_stream.transmitir(origem, io.BytesIO())
    vd = secure_stream.transmitir(destino, io.BytesIO())
    # Simula journal legado criado antes do limite de preflight.
    monkeypatch.setattr(secure_move, 'LIMITE_MOVE_REVISAVEL', 2 * 1024 * 1024)
    journal = secure_move.mover(origem, destino, original, 'antes', 'novo', vo, vd, journal_root)
    with pytest.raises(ValueError, match='revisão integral'):
        secure_move.revisar_local(journal)
    metadados = secure_move.revisar(journal)
    with pytest.raises(ValueError, match='Revisão local ausente'):
        secure_move.recuperar(journal, secure_move._codigo_revisao(metadados), confirmado=True)
    assert not origem.exists() and destino.read_text() == 'novo'


def test_protocolo_json_nao_expoe_previa_nem_autorizacao(tmp_path):
    dados = preparar(tmp_path)
    journal = executar(dados)
    processo = subprocess.run(
        [sys.executable, '-m', 'passe1_engine.secure_move'],
        input=json.dumps({'operacao': 'revisar', 'journal': str(journal)}),
        text=True, capture_output=True, cwd=Path(__file__).resolve().parents[1], check=False,
    )
    assert processo.returncode == 0
    resultado = json.loads(processo.stdout)
    assert 'previa' not in resultado and 'revisao' not in resultado
    assert 'origem aprovada' not in processo.stdout
    assert 'destino aprovado' not in processo.stdout


def test_isolamento_falha_fechado_sem_sobrescrever_terceiro(tmp_path, monkeypatch):
    origem, destino, _, *_ = (dados := preparar(tmp_path))
    journal = executar(dados)
    plano = secure_move.revisar_local(journal)
    manifesto = json.loads((journal / 'manifesto.json').read_text())
    backup = destino.parent / manifesto['hold_destino'] / 'destino'
    original = secure_move._exclusivo
    chamadas = []
    def trocar_e_bloquear(*args):
        chamadas.append(True)
        if len(chamadas) == 2:
            backup.rename(backup.with_name('destino-real'))
            backup.write_text('terceiro')
        if len(chamadas) == 3:
            raise FileExistsError('quarentena ocupada')
        return original(*args)
    monkeypatch.setattr(secure_move, '_exclusivo', trocar_e_bloquear)
    with pytest.raises(RuntimeError, match='Não foi possível isolar'):
        secure_move.recuperar(journal, plano['revisao'], confirmado=True)
    assert destino.read_text() == 'terceiro'
    assert backup.with_name('destino-real').read_text() == 'destino aprovado\n'
    assert (origem.parent / manifesto['hold_origem'] / 'origem').read_text() == 'origem aprovada\n'
    assert journal.exists()


def test_troca_durante_isolamento_preserva_as_duas_entradas(tmp_path, monkeypatch):
    origem, destino, _, *_ = (dados := preparar(tmp_path))
    journal = executar(dados)
    plano = secure_move.revisar_local(journal)
    manifesto = json.loads((journal / 'manifesto.json').read_text())
    backup = destino.parent / manifesto['hold_destino'] / 'destino'
    original = secure_move._exclusivo
    chamadas = []
    def trocar_duas_vezes(*args):
        chamadas.append(True)
        if len(chamadas) == 2:
            backup.rename(backup.with_name('destino-real'))
            backup.write_text('primeiro terceiro')
        if len(chamadas) == 3:
            destino.rename(tmp_path / 'primeiro-terceiro')
            destino.write_text('segundo terceiro')
        return original(*args)
    monkeypatch.setattr(secure_move, '_exclusivo', trocar_duas_vezes)
    with pytest.raises(RuntimeError, match='entrada diferente'):
        secure_move.recuperar(journal, plano['revisao'], confirmado=True)
    assert not destino.exists()
    assert (tmp_path / 'primeiro-terceiro').read_text() == 'primeiro terceiro'
    (incidente,) = backup.parent.glob('incidente-*')
    assert incidente.read_text() == 'segundo terceiro'
    assert backup.with_name('destino-real').read_text() == 'destino aprovado\n'
    assert (origem.parent / manifesto['hold_origem'] / 'origem').read_text() == 'origem aprovada\n'


def test_symlink_instalado_por_troca_volta_a_quarentena_sem_seguir(tmp_path, monkeypatch):
    origem, destino, _, *_ = (dados := preparar(tmp_path))
    journal = executar(dados)
    plano = secure_move.revisar_local(journal)
    manifesto = json.loads((journal / 'manifesto.json').read_text())
    backup = destino.parent / manifesto['hold_destino'] / 'destino'
    externo = tmp_path / 'externo'
    externo.write_text('não tocar')
    original = secure_move._exclusivo
    chamadas = []
    def trocar(*args):
        chamadas.append(True)
        if len(chamadas) == 2:
            backup.rename(backup.with_name('destino-real'))
            backup.symlink_to(externo)
        return original(*args)
    monkeypatch.setattr(secure_move, '_exclusivo', trocar)
    with pytest.raises(RuntimeError, match='Entrada inesperada movida para quarentena'):
        secure_move.recuperar(journal, plano['revisao'], confirmado=True)
    assert not destino.exists()
    (incidente,) = backup.parent.glob('incidente-*')
    assert incidente.is_symlink()
    assert externo.read_text() == 'não tocar'
    assert backup.with_name('destino-real').read_text() == 'destino aprovado\n'
    assert (origem.parent / manifesto['hold_origem'] / 'origem').read_text() == 'origem aprovada\n'


def preparar_destino_ausente(tmp_path, *, aninhado=False):
    from passe1_engine import secure_write
    origem = tmp_path / 'origem'
    destino = tmp_path / 'novo' / 'sub' / 'destino' if aninhado else tmp_path / 'destino'
    journal = tmp_path / 'journals'
    journal.mkdir(mode=0o700)
    origem.write_text('origem aprovada\n')
    return origem, destino, journal, secure_stream.transmitir(origem, io.BytesIO()), secure_write.preparar_criacao(destino)


def executar_destino_ausente(dados):
    origem, destino, journal, vo, vd = dados
    return secure_move.mover(origem, destino, 'origem aprovada\n', None, 'resultado\n', vo, vd, journal)


def test_move_para_destino_ausente_e_recuperacao_confirmada(tmp_path):
    dados = preparar_destino_ausente(tmp_path)
    origem, destino, _, *_ = dados
    journal = executar_destino_ausente(dados)
    assert not origem.exists() and destino.read_text() == 'resultado\n'
    plano = secure_move.revisar_local(journal)
    assert plano['destino_ausente'] is True
    assert [acao['acao'] for acao in plano['acoes']] == ['guardar_resultado', 'restaurar_origem']
    assert '-resultado' in plano['previa'] and '+origem aprovada' in plano['previa']
    secure_move.recuperar(journal, plano['revisao'], confirmado=True)
    assert origem.read_text() == 'origem aprovada\n' and not destino.exists()


def test_move_para_destino_ausente_cria_pais_e_permite_recuperacao(tmp_path):
    dados = preparar_destino_ausente(tmp_path, aninhado=True)
    origem, destino, _, *_ = dados
    assert not destino.parent.exists()
    journal = executar_destino_ausente(dados)
    assert destino.read_text() == 'resultado\n'
    recuperar_confirmado(journal)
    assert origem.read_text() == 'origem aprovada\n' and not destino.exists()


def test_terceiro_cria_destino_antes_de_mover_sem_sobrescrita(tmp_path):
    dados = preparar_destino_ausente(tmp_path)
    origem, destino, _, *_ = dados
    destino.write_text('terceiro')
    with pytest.raises(RuntimeError, match='journal preservado'):
        executar_destino_ausente(dados)
    assert origem.read_text() == 'origem aprovada\n' and destino.read_text() == 'terceiro'


def test_terceiro_insere_destino_na_publicacao_sem_sobrescrita(tmp_path, monkeypatch):
    dados = preparar_destino_ausente(tmp_path)
    origem, destino, journal_root, *_ = dados
    original = secure_move._exclusivo
    chamadas = []
    def inserir(*args):
        chamadas.append(True)
        if len(chamadas) == 2:
            destino.write_text('terceiro')
        return original(*args)
    monkeypatch.setattr(secure_move, '_exclusivo', inserir)
    with pytest.raises(RuntimeError, match='journal preservado'):
        executar_destino_ausente(dados)
    assert not origem.exists() and destino.read_text() == 'terceiro'
    (journal,) = journal_root.iterdir()
    manifesto = json.loads((journal / 'manifesto.json').read_text())
    assert (origem.parent / manifesto['hold_origem'] / 'origem').read_text() == 'origem aprovada\n'
    with pytest.raises(ValueError, match='Destino foi alterado'):
        secure_move.revisar_local(journal)
    assert destino.read_text() == 'terceiro'
    destino.rename(tmp_path / 'terceiro-preservado')
    recuperar_confirmado(journal)
    assert origem.read_text() == 'origem aprovada\n' and not destino.exists()
    assert (tmp_path / 'terceiro-preservado').read_text() == 'terceiro'


def test_falha_depois_de_publicar_destino_ausente_tem_recuperacao(tmp_path, monkeypatch):
    dados = preparar_destino_ausente(tmp_path)
    origem, destino, journal_root, *_ = dados
    original = secure_move._manifesto
    def falhar(fd, info):
        if info.get('fase') == 'aplicado':
            raise OSError('falha simulada')
        return original(fd, info)
    monkeypatch.setattr(secure_move, '_manifesto', falhar)
    with pytest.raises(RuntimeError, match='journal preservado'):
        executar_destino_ausente(dados)
    assert not origem.exists() and destino.read_text() == 'resultado\n'
    (journal,) = journal_root.iterdir()
    recuperar_confirmado(journal)
    assert origem.read_text() == 'origem aprovada\n' and not destino.exists()


def test_crash_depois_de_publicar_destino_ausente_tem_recuperacao(tmp_path):
    origem, destino, journal_root, vo, vd = preparar_destino_ausente(tmp_path)
    codigo = '''
import json, os, sys
from passe1_engine import secure_move
original = secure_move._manifesto
def interromper(fd, dados):
    if dados.get('fase') == 'aplicado':
        os._exit(91)
    original(fd, dados)
secure_move._manifesto = interromper
secure_move.mover(sys.argv[1], sys.argv[2], 'origem aprovada\\n', None, 'resultado\\n', json.loads(sys.argv[3]), json.loads(sys.argv[4]), sys.argv[5])
'''
    processo = subprocess.run([sys.executable, '-c', codigo, str(origem), str(destino), json.dumps(vo), json.dumps(vd), str(journal_root)], cwd=Path(__file__).resolve().parents[1], check=False)
    assert processo.returncode == 91
    assert not origem.exists() and destino.read_text() == 'resultado\n'
    (journal,) = journal_root.iterdir()
    recuperar_confirmado(journal)
    assert origem.read_text() == 'origem aprovada\n' and not destino.exists()


def test_troca_de_diretorio_do_destino_ausente_recusa_sem_perda(tmp_path):
    from passe1_engine import secure_write
    origem = tmp_path / 'origem'
    origem.write_text('origem aprovada\n')
    pasta = tmp_path / 'pasta'
    pasta.mkdir()
    destino = pasta / 'destino'
    journal_root = tmp_path / 'journals'
    journal_root.mkdir(mode=0o700)
    vo = secure_stream.transmitir(origem, io.BytesIO())
    vd = secure_write.preparar_criacao(destino)
    pasta.rename(tmp_path / 'pasta-original')
    pasta.mkdir()
    with pytest.raises(RuntimeError, match='journal preservado'):
        secure_move.mover(origem, destino, 'origem aprovada\n', None, 'resultado\n', vo, vd, journal_root)
    assert origem.read_text() == 'origem aprovada\n' and not destino.exists()


def test_move_e_criacao_posterior_reutilizam_pais_do_proprio_lote(tmp_path):
    from passe1_engine import secure_write
    origem, destino, journal_root, vo, vd = preparar_destino_ausente(tmp_path, aninhado=True)
    outro = destino.parent / 'outro'
    identidade_outro = secure_write.preparar_criacao(outro)
    resultado = secure_move.mover(origem, destino, 'origem aprovada\n', None, 'resultado\n', vo, vd, journal_root, retornar_estado=True)
    assert len(resultado['pais']) > len(identidade_outro['pais'])
    secure_write.substituir(outro, None, 'segundo', {'pais': resultado['pais']})
    assert destino.read_text() == 'resultado\n'
    assert outro.read_text() == 'segundo'
    assert Path(resultado['journal']).exists()


def test_pais_reutilizados_recusam_diretorio_trocado_entre_operacoes(tmp_path):
    from passe1_engine import secure_write
    origem, destino, journal_root, vo, vd = preparar_destino_ausente(tmp_path, aninhado=True)
    outro = destino.parent / 'outro'
    resultado = secure_move.mover(origem, destino, 'origem aprovada\n', None, 'resultado\n', vo, vd, journal_root, retornar_estado=True)
    pasta = destino.parent
    pasta.rename(pasta.with_name('original'))
    pasta.mkdir()
    with pytest.raises(ValueError, match='Diretório mudou'):
        secure_write.substituir(outro, None, 'segundo', {'pais': resultado['pais']})
    assert not outro.exists()
    assert (pasta.with_name('original') / 'destino').read_text() == 'resultado\n'


def test_protocolo_move_retorna_journal_e_identidades_dos_pais(tmp_path):
    origem, destino, journal_root, vo, vd = preparar_destino_ausente(tmp_path, aninhado=True)
    entrada = {'operacao': 'mover', 'origem': str(origem), 'destino': str(destino), 'anterior_origem': 'origem aprovada\n',
               'anterior_destino': None, 'novo': 'resultado\n', 'identidade_origem': vo, 'identidade_destino': vd,
               'journal_root': str(journal_root)}
    processo = subprocess.run([sys.executable, '-m', 'passe1_engine.secure_move'], input=json.dumps(entrada), text=True,
                              capture_output=True, cwd=Path(__file__).resolve().parents[1], check=False)
    assert processo.returncode == 0, processo.stderr
    resultado = json.loads(processo.stdout)
    assert Path(resultado['journal']).exists() and resultado['quarentena_mantida'] is True
    assert len(resultado['pais']) > len(vd['pais'])
    assert all(all(isinstance(valor, str) for valor in par) for par in resultado['pais'])


def test_symlink_insere_destino_ausente_sem_seguir_ou_alterar_terceiro(tmp_path, monkeypatch):
    dados = preparar_destino_ausente(tmp_path)
    origem, destino, journal_root, *_ = dados
    externo = tmp_path / 'externo'
    externo.write_text('preservado')
    original = secure_move._exclusivo
    chamadas = []
    def inserir(*args):
        chamadas.append(True)
        if len(chamadas) == 2:
            destino.symlink_to(externo)
        return original(*args)
    monkeypatch.setattr(secure_move, '_exclusivo', inserir)
    with pytest.raises(RuntimeError, match='journal preservado'):
        executar_destino_ausente(dados)
    assert destino.is_symlink() and externo.read_text() == 'preservado'
    (journal,) = journal_root.iterdir()
    manifesto = json.loads((journal / 'manifesto.json').read_text())
    assert (origem.parent / manifesto['hold_origem'] / 'origem').read_text() == 'origem aprovada\n'
    with pytest.raises(ValueError):
        secure_move.revisar_local(journal)
    assert externo.read_text() == 'preservado'


def test_move_recusa_conteudo_excessivo_antes_de_alterar_arquivos(tmp_path):
    dados = preparar(tmp_path)
    origem, destino, journal, id_origem, id_destino = dados
    grande = 'x' * (1024 * 1024)
    with pytest.raises(ValueError, match='excede 1 MiB'):
        secure_move.mover(origem, destino, origem.read_text(), destino.read_text(), grande, id_origem, id_destino, journal)
    assert origem.read_text() == 'origem aprovada\n'
    assert destino.read_text() == 'destino aprovado\n'
    assert not list(journal.iterdir())
