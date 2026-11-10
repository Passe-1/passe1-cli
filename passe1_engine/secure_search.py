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
    """Pesquisa stdin ancorado e só retorna resultados após validação do handle."""
    import json
    import subprocess
    import threading

    resultados = []
    for nome in dict.fromkeys(caminhos):
        caminho = Path(nome)
        if _sensivel(caminho):
            continue
        processo = None
        leitor = None
        locais = []
        erros = []
        try:
            processo = subprocess.Popen(
                [ripgrep, '--no-config', '--json', '--max-columns', '2000',
                 '--max-columns-preview', '--', padrao, '-'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            def ler_saida():
                try:
                    for linha in processo.stdout:
                        evento = json.loads(linha)
                        if evento.get('type') != 'match' or len(locais) >= limite:
                            continue
                        dado = evento['data']
                        texto = dado['lines'].get('text')
                        if texto is not None:
                            locais.append((nome, dado['line_number'], texto.rstrip('\r\n')))
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    erros.append(exc)
            leitor = threading.Thread(target=ler_saida, daemon=True)
            leitor.start()
            try:
                secure_stream.transmitir(caminho, processo.stdin)
            except BrokenPipeError:
                codigo = processo.wait()
                leitor.join()
                if codigo not in (0, 1):
                    raise ValueError('Ripgrep recusou a expressão de busca.')
                continue
            except ValueError:
                continue
            processo.stdin.close()
            codigo = processo.wait()
            leitor.join()
            if erros or codigo not in (0, 1):
                raise ValueError('Ripgrep recusou a expressão de busca.')
            resultados.extend(locais[:limite - len(resultados)])
            if len(resultados) >= limite:
                return resultados
        except (OSError, RuntimeError):
            continue
        finally:
            if processo is not None:
                if processo.poll() is None:
                    processo.kill()
                    processo.wait()
                if processo.stdin and not processo.stdin.closed:
                    processo.stdin.close()
                if leitor is not None:
                    leitor.join()
                if processo.stdout:
                    processo.stdout.close()
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
