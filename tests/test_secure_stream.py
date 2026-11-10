import io
import os

import pytest

from passe1_engine import secure_stream


def test_stream_grande_sem_limite_de_64_mib(tmp_path):
    arquivo = tmp_path / 'grande'
    with arquivo.open('wb') as f:
        f.seek(65 * 1024 * 1024)
        f.write(b'fim')
    class Contador:
        total = 0
        maior = 0
        def write(self, dado):
            self.total += len(dado)
            self.maior = max(self.maior, len(dado))
    destino = Contador()
    secure_stream.transmitir(arquivo, destino)
    assert destino.total == 65 * 1024 * 1024 + 3
    assert destino.maior <= 65536


def test_recusa_symlink_ancestral(tmp_path):
    externo = tmp_path / 'externo'
    externo.mkdir()
    (externo / 'dados').write_text('segredo')
    (tmp_path / 'projeto').symlink_to(externo, target_is_directory=True)
    with pytest.raises(OSError):
        secure_stream.transmitir(tmp_path / 'projeto' / 'dados', io.BytesIO())


def test_recusa_hardlink(tmp_path):
    original = tmp_path / 'secret'
    original.write_text('segredo')
    os.link(original, tmp_path / 'alias')
    with pytest.raises(ValueError):
        secure_stream.transmitir(tmp_path / 'alias', io.BytesIO())


@pytest.mark.skipif(os.name == 'nt', reason='Windows impede renomear diretório com arquivo aberto')
def test_troca_de_ancestral_durante_leitura_nao_redireciona_descritor(tmp_path):
    projeto = tmp_path / 'projeto'
    projeto.mkdir()
    arquivo = projeto / 'dados'
    arquivo.write_bytes(b'abcd')
    externo = tmp_path / 'externo'
    externo.mkdir()
    (externo / 'dados').write_bytes(b'SEGREDO')
    class Destino(io.BytesIO):
        trocou = False
        def write(self, bloco):
            if not self.trocou:
                self.trocou = True
                projeto.rename(tmp_path / 'projeto-original')
                projeto.symlink_to(externo, target_is_directory=True)
            return super().write(bloco)
    destino = Destino()
    secure_stream.transmitir(arquivo, destino, tamanho_bloco=1)
    assert destino.getvalue() == b'abcd'
