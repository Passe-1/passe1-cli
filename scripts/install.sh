#!/bin/sh
# Instala a release compatível do Passe1. O script Python seleciona o wheel.
set -eu
command -v python3 >/dev/null 2>&1 || { echo 'Instale Python 3 para continuar.' >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo 'Instale curl para continuar.' >&2; exit 1; }
arquivo=$(mktemp)
trap 'rm -f "$arquivo"' EXIT HUP INT TERM
curl -fsSL 'https://github.com/Passe-1/passe1-cli/releases/latest/download/install.py' -o "$arquivo"
python3 "$arquivo"
