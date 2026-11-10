"""Operações do agente não seguem links trocados nem modificam hard links."""
import importlib.util
import os

import pytest


def _arquivos():
    assert importlib.util.find_spec("arquivos_seguros") is not None, "operações seguras ainda não implementadas"
    import arquivos_seguros
    return arquivos_seguros


def test_edicao_revalida_diretorio_trocado_por_symlink(tmp_path):
    arquivos = _arquivos()
    raiz = tmp_path / "projeto"
    pasta = raiz / "pasta"
    pasta.mkdir(parents=True)
    (pasta / "doc.txt").write_text("original")
    fora = tmp_path / "fora"
    fora.mkdir()
    (fora / "doc.txt").write_text("não alterar")
    texto, versao = arquivos.ler(raiz, "pasta/doc.txt")
    assert texto == "original"
    pasta.rename(raiz / "pasta-antiga")
    pasta.symlink_to(fora, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        arquivos.editar(raiz, "pasta/doc.txt", versao, "alterado")
    assert (fora / "doc.txt").read_text() == "não alterar"
    assert (raiz / "pasta-antiga" / "doc.txt").read_text() == "original"


def test_recusa_hard_link_sem_alterar_conteudo(tmp_path):
    arquivos = _arquivos()
    raiz = tmp_path / "projeto"
    raiz.mkdir()
    fora = tmp_path / "externo.txt"
    fora.write_text("protegido")
    os.link(fora, raiz / "copia.txt")
    with pytest.raises((OSError, ValueError)):
        arquivos.ler(raiz, "copia.txt")
    assert fora.read_text() == "protegido"


def test_criacao_falha_se_diretorio_virar_symlink(tmp_path):
    arquivos = _arquivos()
    raiz = tmp_path / "projeto"
    raiz.mkdir()
    fora = tmp_path / "fora"
    fora.mkdir()
    (raiz / "pasta").symlink_to(fora, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        arquivos.criar(raiz, "pasta/novo.txt", "não gravar")
    assert not (fora / "novo.txt").exists()


def test_editar_arquivo_mudado_nao_sobrescreve(tmp_path):
    arquivos = _arquivos()
    alvo = tmp_path / "doc.txt"
    alvo.write_text("antes")
    _, versao = arquivos.ler(tmp_path, "doc.txt")
    alvo.write_text("mudou")
    with pytest.raises((OSError, ValueError)):
        arquivos.editar(tmp_path, "doc.txt", versao, "perderia mudanças")
    assert alvo.read_text() == "mudou"


def test_criar_e_editar_preservam_texto_e_permissoes(tmp_path):
    arquivos = _arquivos()
    arquivos.criar(tmp_path, "dir/nome.txt", "olá\n")
    alvo = tmp_path / "dir" / "nome.txt"
    alvo.chmod(0o640)
    texto, versao = arquivos.ler(tmp_path, "dir/nome.txt")
    assert texto == "olá\n"
    arquivos.editar(tmp_path, "dir/nome.txt", versao, "novo\n")
    assert alvo.read_text() == "novo\n"
    assert alvo.stat().st_mode & 0o777 == 0o640


def test_recusa_caminho_que_sai_da_raiz(tmp_path):
    arquivos = _arquivos()
    fora = tmp_path.parent / "fora-seguro.txt"
    with pytest.raises((OSError, ValueError)):
        arquivos.criar(tmp_path, "../fora-seguro.txt", "não gravar")
    assert not fora.exists()


@pytest.mark.skipif(not os.path.exists('/usr/bin/xattr'), reason='atributos do macOS')
def test_edicao_preserva_atributos_estendidos(tmp_path):
    import subprocess

    arquivos = _arquivos()
    alvo = tmp_path / 'doc.txt'
    alvo.write_text('antes')
    subprocess.run(['/usr/bin/xattr', '-w', 'com.passe1.teste', 'valor', str(alvo)], check=True)
    _, versao = arquivos.ler(tmp_path, 'doc.txt')
    arquivos.editar(tmp_path, 'doc.txt', versao, 'depois')
    r = subprocess.run(['/usr/bin/xattr', '-p', 'com.passe1.teste', str(alvo)], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == 'valor'
    assert alvo.read_text() == 'depois'


def test_recusa_hardlink_criado_apos_leitura(tmp_path):
    arquivos = _arquivos()
    original = tmp_path / 'doc.txt'
    original.write_text('original')
    _, versao = arquivos.ler(tmp_path, 'doc.txt')
    destino = tmp_path.parent / ('hardlink-' + tmp_path.name)
    os.link(original, destino)
    try:
        with pytest.raises((OSError, ValueError)):
            arquivos.editar(tmp_path, 'doc.txt', versao, 'alterado')
        assert destino.read_text() == 'original'
    finally:
        destino.unlink()


def test_edicao_preserva_finais_de_linha_crlf(tmp_path):
    arquivos = _arquivos()
    alvo = tmp_path / 'doc.txt'
    alvo.write_bytes(b'primeira\r\nsegunda\r\n')
    texto, versao = arquivos.ler(tmp_path, 'doc.txt')
    assert texto == 'primeira\r\nsegunda\r\n'
    arquivos.editar(tmp_path, 'doc.txt', versao, texto.replace('segunda', 'terceira'))
    assert alvo.read_bytes() == b'primeira\r\nterceira\r\n'


def test_edicao_atualiza_mtime(tmp_path):
    arquivos = _arquivos()
    alvo = tmp_path / 'doc.txt'
    alvo.write_text('antes')
    os.utime(alvo, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    _, versao = arquivos.ler(tmp_path, 'doc.txt')
    arquivos.editar(tmp_path, 'doc.txt', versao, 'depois')
    assert alvo.stat().st_mtime_ns > 1_600_000_000_000_000_000


def test_falha_na_criacao_nao_remove_arquivo_substituido(tmp_path, monkeypatch):
    arquivos = _arquivos()
    alvo = tmp_path / 'novo.txt'
    def falhar_e_trocar(fd, conteudo):
        os.write(fd, b'incompleto')
        candidato = next(tmp_path.glob('.passe1-*'))
        candidato.unlink()
        candidato.write_text('concorrente')
        raise OSError('falha simulada')
    monkeypatch.setattr(arquivos, '_gravar', falhar_e_trocar)
    with pytest.raises(OSError):
        arquivos.criar(tmp_path, 'novo.txt', 'conteúdo')
    assert not alvo.exists()
    assert [p.read_text() for p in tmp_path.glob('.passe1-*')] == ['concorrente']


@pytest.mark.skipif(not os.path.exists('/usr/bin/xattr'), reason='ACLs do macOS')
def test_edicao_preserva_acl(tmp_path):
    import getpass
    import subprocess

    arquivos = _arquivos()
    alvo = tmp_path / 'doc.txt'
    alvo.write_text('antes')
    subprocess.run(['/bin/chmod', '+a', f'user:{getpass.getuser()} allow read', str(alvo)], check=True)
    def acl():
        saida = subprocess.run(['/bin/ls', '-le', str(alvo)], capture_output=True, text=True, check=True).stdout
        return saida.splitlines()[1:]
    original = acl()
    assert original
    _, versao = arquivos.ler(tmp_path, 'doc.txt')
    arquivos.editar(tmp_path, 'doc.txt', versao, 'depois')
    assert acl() == original
    assert alvo.read_text() == 'depois'


def test_edicao_nao_substitui_por_temporario_trocado(tmp_path, monkeypatch):
    arquivos = _arquivos()
    alvo = tmp_path / 'doc.txt'
    alvo.write_text('original')
    _, versao = arquivos.ler(tmp_path, 'doc.txt')
    copiar = arquivos._copiar_metadados
    def trocar(origem, destino):
        copiar(origem, destino)
        temporario = next(tmp_path.glob('.passe1-*'))
        temporario.unlink()
        temporario.write_text('concorrente')
    monkeypatch.setattr(arquivos, '_copiar_metadados', trocar)
    with pytest.raises((OSError, ValueError)):
        arquivos.editar(tmp_path, 'doc.txt', versao, 'esperado')
    assert alvo.read_text() == 'original'
    assert [p.read_text() for p in tmp_path.glob('.passe1-*')] == ['concorrente']


def test_falha_na_criacao_remove_apenas_proprio_temporario(tmp_path, monkeypatch):
    arquivos = _arquivos()
    def falhar(fd, conteudo):
        os.write(fd, b'incompleto')
        raise OSError('disco cheio')
    monkeypatch.setattr(arquivos, '_gravar', falhar)
    with pytest.raises(OSError):
        arquivos.criar(tmp_path, 'novo.txt', 'conteúdo')
    assert not (tmp_path / 'novo.txt').exists()
    assert list(tmp_path.glob('.passe1-*')) == []


def test_criacao_recusa_diretorio_movido_apos_gravacao(tmp_path, monkeypatch):
    arquivos = _arquivos()
    raiz = tmp_path / 'projeto'
    pasta = raiz / 'pasta'
    pasta.mkdir(parents=True)
    fora = tmp_path / 'fora'
    fora.mkdir()
    gravar = arquivos._gravar

    def mover(fd, conteudo):
        gravar(fd, conteudo)
        pasta.rename(fora / 'movida')
        pasta.mkdir()

    monkeypatch.setattr(arquivos, '_gravar', mover)
    with pytest.raises((OSError, ValueError)):
        arquivos.criar(raiz, 'pasta/novo.txt', 'não publicar fora')
    assert not (fora / 'movida' / 'novo.txt').exists()
    assert not (pasta / 'novo.txt').exists()


def test_edicao_recusa_raiz_movida_e_substituida_por_symlink(tmp_path):
    arquivos = _arquivos()
    raiz = tmp_path / 'projeto'
    raiz.mkdir()
    (raiz / 'doc.txt').write_text('original')
    _, versao = arquivos.ler(raiz, 'doc.txt')
    fora = tmp_path / 'fora'
    fora.mkdir()
    movida = fora / 'movida'
    raiz.rename(movida)
    raiz.symlink_to(movida, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        arquivos.editar(raiz, 'doc.txt', versao, 'não publicar fora')
    assert (movida / 'doc.txt').read_text() == 'original'


def test_criacao_recusa_raiz_movida_e_substituida_por_symlink(tmp_path, monkeypatch):
    arquivos = _arquivos()
    raiz = tmp_path / 'projeto'
    raiz.mkdir()
    fora = tmp_path / 'fora'
    fora.mkdir()
    movida = fora / 'movida'
    gravar = arquivos._gravar

    def mover(fd, conteudo):
        gravar(fd, conteudo)
        raiz.rename(movida)
        raiz.symlink_to(movida, target_is_directory=True)

    monkeypatch.setattr(arquivos, '_gravar', mover)
    with pytest.raises((OSError, ValueError)):
        arquivos.criar(raiz, 'novo.txt', 'não publicar fora')
    assert not (movida / 'novo.txt').exists()


def test_recusa_raiz_ja_substituida_por_symlink(tmp_path):
    arquivos = _arquivos()
    raiz = tmp_path / 'projeto'
    raiz.mkdir()
    fora = tmp_path / 'fora'
    fora.mkdir()
    (fora / 'segredo.txt').write_text('não ler')
    raiz.rename(tmp_path / 'projeto-original')
    raiz.symlink_to(fora, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        arquivos.ler(raiz, 'segredo.txt')
    with pytest.raises((OSError, ValueError)):
        arquivos.criar(raiz, 'novo.txt', 'não gravar')
    assert not (fora / 'novo.txt').exists()
