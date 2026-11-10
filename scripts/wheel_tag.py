"""Tags explícitas para wheels contendo executáveis macOS nativos."""
import re
import subprocess


def minimo_binario(caminho):
    saida = subprocess.check_output(["otool", "-l", str(caminho)], text=True)
    secoes = re.split(r"Load command \d+", saida)
    for secao in secoes:
        if "LC_BUILD_VERSION" in secao:
            match = re.search(r"\bminos (\d+)\.(\d+)", secao)
        elif "LC_VERSION_MIN_MACOSX" in secao:
            match = re.search(r"\bversion (\d+)\.(\d+)", secao)
        else:
            continue
        if match:
            return int(match.group(1)), int(match.group(2))
    raise ValueError(f"Versão mínima macOS ausente em {caminho}")


def tag_macos(tag, maquina, minimos=()):
    if maquina not in {'arm64', 'x86_64'}:
        raise ValueError('Arquitetura macOS desconhecida para o wheel.')
    match = re.fullmatch(r'macosx_(\d+)_(\d+)_(?:universal2|arm64|x86_64)', tag)
    if match is None:
        raise ValueError('Tag macOS incompatível com o binário nativo.')
    versao = max((int(match.group(1)), int(match.group(2))), *minimos) if minimos else (int(match.group(1)), int(match.group(2)))
    if maquina == 'arm64':
        versao = max(versao, (11, 0))
    return f'macosx_{versao[0]}_{versao[1]}_{maquina}'
