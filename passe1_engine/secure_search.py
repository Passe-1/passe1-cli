"""Busca de texto somente a partir de conteúdo lido por handle validado."""
from __future__ import annotations

import fnmatch
import io
import os
import re
from pathlib import Path

from passe1_engine import secure_stream


def _sensivel(caminho):
    partes = [parte.lower() for parte in caminho.parts]
    nome = caminho.name.lower()
    return (any(p in {'.ssh', '.aws', '.gnupg', '.config'} for p in partes)
            or nome in {'.env', '.envrc', '.npmrc', '.pypirc', '.netrc', '.dockerconfigjson', 'credentials', 'token', 'secrets'}
            or nome.startswith(('.env.', 'credentials.', 'secrets.', 'secret.', 'token.', 'private_key', 'id_rsa', 'id_ed25519', 'id_ecdsa', 'service-account'))
            or nome.endswith(('.env', '.pem', '.key', '.p12', '.pfx', '.keystore')))


class _Limitado(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > 1024 * 1024:
            raise ValueError("Arquivo excede o limite de busca.")
        return super().write(data)


def buscar(raiz, padrao, incluir='*', limite=100):
    expressao = re.compile(padrao)
    raiz = Path(os.path.abspath(raiz))
    resultados = []
    for pasta, diretorios, arquivos in os.walk(raiz, followlinks=False):
        diretorios[:] = [nome for nome in diretorios if not nome.startswith('.') and nome not in {'node_modules', 'venv', '__pycache__'}]
        for nome in sorted(arquivos):
            caminho = Path(pasta) / nome
            if nome.startswith('.') or not fnmatch.fnmatch(nome, incluir) or _sensivel(caminho):
                continue
            try:
                dados = _Limitado()
                secure_stream.transmitir(caminho, dados)
                texto = dados.getvalue().decode('utf-8')
                if '\0' in texto:
                    continue
                for numero, linha in enumerate(texto.splitlines(), 1):
                    if expressao.search(linha):
                        resultados.append((str(caminho), numero, linha[:2000]))
                        if len(resultados) >= limite:
                            return resultados
            except (OSError, ValueError, UnicodeError, RuntimeError):
                continue
    return resultados


def buscar_candidatos(padrao, caminhos, ripgrep, limite=100):
    """Reexecuta ripgrep em cópias verificadas; nunca usa o texto da busca inicial."""
    import json
    import subprocess
    import tempfile

    resultados = []
    for nome in dict.fromkeys(caminhos):
        caminho = Path(nome)
        if _sensivel(caminho):
            continue
        try:
            with tempfile.TemporaryDirectory(prefix='passe1-grep-') as privado, \
                    tempfile.SpooledTemporaryFile(max_size=1024 * 1024, dir=privado) as seguro:
                try:
                    secure_stream.transmitir(caminho, seguro)
                except (OSError, ValueError, RuntimeError):
                    continue
                seguro.seek(0)
                processo = subprocess.run(
                    [ripgrep, '--no-config', '--json', '--max-count', str(limite),
                     '--max-columns', '2000', '--max-columns-preview', '--', padrao, '-'],
                    stdin=seguro, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                )
            if processo.returncode not in (0, 1):
                raise ValueError('Ripgrep recusou a expressão de busca.')
            for linha in processo.stdout.splitlines():
                evento = json.loads(linha)
                if evento.get('type') != 'match':
                    continue
                dado = evento['data']
                texto = dado['lines'].get('text')
                if texto is None:
                    continue
                resultados.append((nome, dado['line_number'], texto.rstrip('\r\n')))
                if len(resultados) >= limite:
                    return resultados
        except (OSError, UnicodeError, RuntimeError):
            continue
    return resultados


def _main():
    import json
    import sys
    try:
        dados = json.load(sys.stdin)
        resultado = buscar_candidatos(dados['padrao'], dados['caminhos'], dados['ripgrep'])
        json.dump(resultado, sys.stdout)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Busca segura recusada: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
