"""Anexos escolhidos pelo usuário, lidos sem seguir links ou reabrir arquivos."""
from __future__ import annotations

import base64
import contextlib
import io
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from passe1_engine.secure_stream import transmitir

LIMITE = 4 * 1024 * 1024
LIMITE_TEXTO = 256 * 1024


class ConsentimentoNecessario(ValueError):
    pass


@dataclass(frozen=True)
class Anexo:
    nome: str
    parte: dict
    tamanho: int


class _Limitado(io.BytesIO):
    def write(self, dados):
        if self.tell() + len(dados) > LIMITE:
            raise ValueError('Anexo ultrapassa o limite de 4 MiB.')
        return super().write(dados)


def _caminho(texto):
    if texto.startswith(('\\\\', '//')):
        raise ValueError('Caminhos remotos ou de dispositivo não podem ser anexados.')
    if texto.startswith('file://'):
        url = urlsplit(texto)
        if url.netloc not in {'', 'localhost'} or url.query or url.fragment:
            raise ValueError('URL de arquivo remoto ou inválida.')
        texto = unquote(url.path)
        if os.name == 'nt' and re.match(r'^/[A-Za-z]:', texto):
            texto = texto[1:]
    if texto.startswith(("\\\\", "//")):
        raise ValueError("Caminhos remotos ou de dispositivo não podem ser anexados.")
    alvo = Path(os.path.abspath(os.path.expanduser(texto)))
    if sys.platform == 'darwin':
        for alias in ('/tmp', '/var'):
            if str(alvo).startswith(alias + '/') and os.path.islink(alias) and os.readlink(alias) == 'private/' + alias[1:]:
                alvo = Path('/private') / alvo.relative_to('/')
    return alvo


def extrair_caminhos(texto):
    """Só reconhece colagem que contém exclusivamente arquivos existentes."""
    texto = texto.strip()
    if not texto:
        return []
    try:
        direto = _caminho(texto)
        if direto.is_file():
            return [str(direto)]
        nomes = shlex.split(texto, posix=os.name != 'nt')
        caminhos = [_caminho(nome.strip('"')) for nome in nomes]
        return [str(p) for p in caminhos] if caminhos and all(p.is_file() for p in caminhos) else []
    except (OSError, ValueError):
        return []


def _sensivel(caminho):
    padrao = r'(^|[._-])(secret|secrets|token|credentials|private_key)([._-]|$)'
    return any(p.lower() in {'.ssh', '.aws', '.gnupg', '.config', '.npmrc', '.pypirc', '.netrc'}
               or p.lower().startswith('.env') or re.search(padrao, p.lower())
               or p.lower().endswith(('.pem', '.key', '.p12', '.pfx', '.keystore'))
               or p.lower().startswith(('id_rsa', 'id_ed25519', 'id_ecdsa')) for p in caminho.parts)


def _imagem(dados, nome):
    if dados.startswith(b'\x89PNG\r\n\x1a\n'):
        mime = 'image/png'
    elif dados.startswith(b'\xff\xd8\xff'):
        mime = 'image/jpeg'
    elif dados.startswith((b'GIF87a', b'GIF89a')):
        mime = 'image/gif'
    elif dados.startswith(b'RIFF') and dados[8:12] == b'WEBP':
        mime = 'image/webp'
    else:
        return None
    return Anexo(nome, {'type': 'file', 'mime': mime, 'filename': nome,
                       'url': f'data:{mime};base64,' + base64.b64encode(dados).decode('ascii')}, len(dados))


@contextlib.contextmanager
def _abrir(alvo):
    if os.name == 'nt':
        from passe1_engine import win_secure
        with win_secure.abrir_leitura(alvo) as (fd, identidade):
            yield fd, identidade
    else:
        import arquivos_seguros
        with arquivos_seguros._pai(Path(alvo.anchor), alvo) as (pai, nome, pais):
            fd, _ = arquivos_seguros._abrir_arquivo(pai, nome)
            try:
                yield fd, pais
            finally:
                os.close(fd)


class AnexoPendente:
    def __init__(self, caminho):
        import arquivos_seguros
        self.caminho = _caminho(str(caminho))
        self._contexto = _abrir(self.caminho)
        self._fd, self._pais = self._contexto.__enter__()
        self._estado = arquivos_seguros._estado(os.fstat(self._fd))
        self._fechado = False

    def confirmar(self):
        import arquivos_seguros
        from passe1_engine.secure_stream import _transmitir_fd
        if self._fechado:
            raise ValueError('Anexo pendente já encerrado.')
        try:
            with _abrir(self.caminho) as (atual, pais):
                if pais != self._pais or arquivos_seguros._estado(os.fstat(atual)) != self._estado:
                    raise ValueError('Arquivo mudou após a solicitação de confirmação.')
            if arquivos_seguros._estado(os.fstat(self._fd)) != self._estado:
                raise ValueError('Arquivo mudou após a solicitação de confirmação.')
            destino = _Limitado()
            _transmitir_fd(self._fd, destino, 65536)
            return _criar_anexo(destino.getvalue(), self.caminho)
        finally:
            self.fechar()

    def fechar(self):
        if not self._fechado:
            self._fechado = True
            self._contexto.__exit__(None, None, None)


def preparar_pendente(caminho):
    return AnexoPendente(caminho)


def preparar(caminho, confirmar=None):
    alvo = _caminho(str(caminho))
    if _sensivel(alvo):
        if confirmar is None:
            raise ConsentimentoNecessario('Arquivo potencialmente sensível; confirme antes de anexar.')
        pendente = preparar_pendente(alvo)
        try:
            if not confirmar(alvo):
                raise ConsentimentoNecessario('Arquivo potencialmente sensível; confirme antes de anexar.')
            return pendente.confirmar()
        finally:
            pendente.fechar()
    destino = _Limitado()
    try:
        transmitir(alvo, destino)
    except (OSError, RuntimeError) as exc:
        raise ValueError('Não foi possível ler o anexo com segurança; links e arquivos em uso são recusados.') from exc
    return _criar_anexo(destino.getvalue(), alvo)


def _criar_anexo(dados, alvo):
    imagem = _imagem(dados, alvo.name)
    if imagem:
        return imagem
    if len(dados) > LIMITE_TEXTO:
        raise ValueError('Arquivo de texto ultrapassa o limite de 256 KiB.')
    try:
        texto = dados.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('Formato não suportado. Anexe texto UTF-8, PNG, JPEG, GIF ou WebP.') from exc
    if '\x00' in texto:
        raise ValueError('Arquivo binário não pode ser anexado como texto.')
    nome = ''.join(c for c in alvo.name if c.isprintable())
    return Anexo(nome, {'type': 'text', 'text': f'Arquivo anexado: {nome}\n<arquivo>\n{texto}\n</arquivo>'}, len(dados))


def _capturar(comando, limite=LIMITE * 3, timeout=5):
    processo = subprocess.Popen(comando, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    concluido = threading.Event()
    resultado = []

    def ler():
        try:
            resultado.append(processo.stdout.read(limite + 1))
        except (OSError, ValueError):
            resultado.append(None)
        finally:
            concluido.set()

    leitor = threading.Thread(target=ler, daemon=True)
    leitor.start()
    try:
        if not concluido.wait(timeout):
            return None
        dados = resultado[0]
        if dados is None:
            return None
        if len(dados) > limite:
            raise ValueError('Imagem da área de transferência ultrapassa 4 MiB.')
        try:
            codigo = processo.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        return dados if codigo == 0 else None
    finally:
        if processo.poll() is None:
            processo.kill()
            processo.wait()
        processo.stdout.close()
        leitor.join(timeout=1)


def ler_clipboard():
    """Retorna imagem da área de transferência sem alterar seu conteúdo."""
    if sys.platform == 'darwin':
        comando = ['pngpaste', '-'] if shutil.which('pngpaste') else ['osascript', '-e', 'get the clipboard as «class PNGf»']
    elif os.name == 'nt':
        comando = ['powershell.exe', '-NoProfile', '-STA', '-Command',
                   'Add-Type -AssemblyName System.Windows.Forms; $i=[Windows.Forms.Clipboard]::GetImage(); if($null -eq $i){exit 2}; $s=New-Object IO.MemoryStream; $i.Save($s,[Drawing.Imaging.ImageFormat]::Png); [Console]::Write([Convert]::ToBase64String($s.ToArray())); $i.Dispose(); $s.Dispose()']
    elif shutil.which('wl-paste'):
        comando = ['wl-paste', '--no-newline', '--type', 'image/png']
    elif shutil.which('xclip'):
        comando = ['xclip', '-selection', 'clipboard', '-t', 'image/png', '-o']
    else:
        return None
    try:
        dados = _capturar(comando)
    except OSError:
        return None
    if dados is None:
        return None
    try:
        if os.name == 'nt':
            dados = base64.b64decode(dados, validate=True)
        elif comando[0] == 'osascript':
            match = re.fullmatch(rb'\s*\xc2\xabdata PNGf([0-9A-Fa-f]+)\xc2\xbb\s*', dados)
            if not match:
                return None
            dados = bytes.fromhex(match.group(1).decode('ascii'))
    except ValueError:
        return None
    if len(dados) > LIMITE:
        raise ValueError('Imagem da área de transferência ultrapassa 4 MiB.')
    return _imagem(dados, 'imagem-colada.png')
