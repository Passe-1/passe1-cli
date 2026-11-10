"""Compara spawn e exec do motor instalado sem credenciais ou gateway."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    if os.name != 'nt':
        return
    localizador = 'import pathlib,passe1_engine; print(pathlib.Path(passe1_engine.__file__).parent / "bin" / "opencode.exe")'
    binario = subprocess.run([sys.executable, '-I', '-c', localizador], capture_output=True, text=True, encoding='utf-8', timeout=30, check=True).stdout.strip()
    if not Path(binario).is_file():
        raise RuntimeError('Motor instalado não encontrado.')
    codigo = 'import os,sys; os.execvpe(sys.argv[1], sys.argv[1:], os.environ)'
    with tempfile.TemporaryDirectory() as pasta:
        ambiente = {chave: valor for chave, valor in os.environ.items() if chave.upper() in {'SYSTEMROOT', 'WINDIR', 'PATH', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE'}}
        ambiente.update({
            'XDG_CONFIG_HOME': str(Path(pasta) / 'config'),
            'XDG_DATA_HOME': str(Path(pasta) / 'data'),
            'XDG_CACHE_HOME': str(Path(pasta) / 'cache'),
            'XDG_STATE_HOME': str(Path(pasta) / 'state'),
            'OPENCODE_DISABLE_MODELS_FETCH': '1',
            'OPENCODE_DISABLE_AUTOUPDATE': '1',
            'OPENCODE_DISABLE_DEFAULT_PLUGINS': '1',
        })
        for argumentos in (['--version'], ['session', 'list', '--format', 'json', '--pure']):
            resultados = {}
            for modo, comando in (
                ('spawn', [binario, *argumentos]),
                ('exec', [sys.executable, '-I', '-c', codigo, binario, *argumentos]),
            ):
                processo = subprocess.run(comando, cwd=pasta, env=ambiente, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60, check=False)
                resultados[modo] = {'codigo': processo.returncode, 'stdout': processo.stdout[-500:], 'stderr': processo.stderr[-500:]}
            print(f'Diagnóstico {argumentos!r}: {json.dumps(resultados, ensure_ascii=True)}')
            if resultados['spawn']['codigo'] != 0:
                raise RuntimeError('Falhou execução do motor via supervisor.')
    print('Diagnóstico de execução do motor concluído sem credenciais.')


if __name__ == '__main__':
    main()
