"""Tags explícitas para wheels contendo executáveis macOS nativos."""
import re


def tag_macos(tag, maquina):
    if maquina not in {'arm64', 'x86_64'}:
        raise ValueError('Arquitetura macOS desconhecida para o wheel.')
    match = re.fullmatch(r'macosx_(\d+)_(\d+)_(?:universal2|arm64|x86_64)', tag)
    if match is None:
        raise ValueError('Tag macOS incompatível com o binário nativo.')
    versao = (int(match.group(1)), int(match.group(2)))
    if maquina == 'arm64':
        versao = max(versao, (11, 0))
    return f'macosx_{versao[0]}_{versao[1]}_{maquina}'
